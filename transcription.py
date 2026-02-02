"""
Service de transcription audio (local et API)
"""

import io
import time
import logging
from typing import Union, Optional
from threading import Lock
from io import BytesIO

import numpy as np
import whisper
from pydub import AudioSegment
from openai import OpenAI

from config import AppConfig
from exceptions import DailyQuotaExceededError, AudioProcessingError

logger = logging.getLogger(__name__)


class RateLimiter:
    """Limiteur de débit pour les appels API"""
    
    def __init__(self, calls_per_minute: int = 50):
        self.interval = 60.0 / calls_per_minute
        self.lock = Lock()
        self.last_call = 0.0
    
    def wait(self) -> None:
        """Attend si nécessaire pour respecter le taux limite"""
        with self.lock:
            now = time.time()
            elapsed = now - self.last_call
            sleep_time = self.interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
            self.last_call = time.time()


class AudioConverter:
    """Convertisseur audio vers différents formats"""
    
    @staticmethod
    def to_numpy(audio: AudioSegment) -> np.ndarray:
        """
        Convertit un AudioSegment en tableau numpy normalisé
        
        Args:
            audio: Segment audio à convertir
            
        Returns:
            Tableau numpy normalisé entre -1 et 1
        """
        audio_np = np.array(audio.get_array_of_samples(), dtype=np.float32)
        audio_np = audio_np / np.iinfo(audio.array_type).max
        
        # Convertir en mono si nécessaire
        if audio.channels > 1:
            audio_np = audio_np.reshape((-1, audio.channels)).mean(axis=1)
        
        return audio_np
    
    @staticmethod
    def to_wav_buffer(audio: AudioSegment) -> BytesIO:
        """
        Convertit un AudioSegment en buffer WAV
        
        Args:
            audio: Segment audio à convertir
            
        Returns:
            Buffer contenant le fichier WAV
        """
        buf = io.BytesIO()
        audio.export(buf, format="wav")
        buf.seek(0)
        return buf


class WhisperTranscriber:
    """Transcription avec Whisper en local"""
    
    def __init__(self):
        self._model_cache = {}
    
    def _load_model(self, model_name: str) -> whisper.Whisper:
        """
        Charge un modèle Whisper avec cache
        
        Args:
            model_name: Nom du modèle à charger
            
        Returns:
            Modèle Whisper chargé
        """
        if model_name not in self._model_cache:
            logger.info(f"Chargement du modèle Whisper: {model_name}")
            self._model_cache[model_name] = whisper.load_model(model_name)
        return self._model_cache[model_name]
    
    def transcribe(
        self,
        audio_input: Union[str, AudioSegment, BytesIO],
        model_name: str
    ) -> str:
        """
        Transcrit un audio avec Whisper
        
        Args:
            audio_input: Fichier audio, AudioSegment ou buffer
            model_name: Nom du modèle Whisper à utiliser
            
        Returns:
            Texte transcrit
            
        Raises:
            AudioProcessingError: En cas d'erreur de traitement
        """
        try:
            model = self._load_model(model_name)
            
            # Gérer les différents types d'entrée
            if isinstance(audio_input, AudioSegment):
                audio = audio_input
            elif isinstance(audio_input, str):
                audio = AudioSegment.from_file(audio_input)
            else:
                if hasattr(audio_input, "getvalue"):
                    audio = AudioSegment.from_file(BytesIO(audio_input.getvalue()))
                else:
                    audio = AudioSegment.from_file(audio_input)
            
            audio_np = AudioConverter.to_numpy(audio)
            result = model.transcribe(audio_np)
            
            return result["text"]
        
        except Exception as e:
            logger.error(f"Erreur lors de la transcription Whisper: {e}")
            raise AudioProcessingError(f"Échec de la transcription: {e}") from e


class APITranscriber:
    """Transcription via API OpenAI"""
    
    def __init__(
        self,
        client: OpenAI,
        language: str = "fr",
        temperature: float = 0.0,
        max_retries: int = 5
    ):
        self.client = client
        self.language = language
        self.temperature = temperature
        self.max_retries = max_retries
        self.rate_limiter = RateLimiter()
    
    def transcribe(self, audio_chunk: AudioSegment, model_name: str) -> str:
        """
        Transcrit un chunk audio via l'API
        
        Args:
            audio_chunk: Segment audio à transcrire
            model_name: Nom du modèle API à utiliser
            
        Returns:
            Texte transcrit
            
        Raises:
            DailyQuotaExceededError: Si le quota journalier est dépassé
            AudioProcessingError: En cas d'autre erreur
        """
        buf = AudioConverter.to_wav_buffer(audio_chunk)
        
        for attempt in range(self.max_retries):
            try:
                self.rate_limiter.wait()
                
                response = self.client.audio.transcriptions.create(
                    file=buf,
                    model=model_name,
                    language=self.language,
                    response_format="json",
                    temperature=self.temperature
                )
                
                return response.text.strip()
            
            except Exception as e:
                error_msg = str(e).lower()
                
                # Vérifier le quota journalier
                if "per day" in error_msg:
                    logger.error("Quota journalier API dépassé")
                    raise DailyQuotaExceededError(str(e)) from e
                
                # Attendre en cas de rate limit
                if "rate limit" in error_msg:
                    wait_time = 2 * (attempt + 1)
                    logger.warning(f"Rate limit atteinte, attente de {wait_time}s")
                    time.sleep(wait_time)
                    continue
                
                # Autre erreur
                logger.error(f"Erreur API (tentative {attempt + 1}/{self.max_retries}): {e}")
                if attempt == self.max_retries - 1:
                    raise AudioProcessingError(f"Échec après {self.max_retries} tentatives: {e}") from e
        
        return ""


class TranscriptionService:
    """Service unifié de transcription"""
    
    def __init__(self, config: AppConfig, client: Optional[OpenAI] = None):
        self.config = config
        
        if config.local_mode:
            self.transcriber = WhisperTranscriber()
        else:
            if not client:
                raise ValueError("Client OpenAI requis pour le mode API")
            self.transcriber = APITranscriber(
                client=client,
                language=config.audio.language,
                temperature=config.audio.temperature
            )
    
    def transcribe(
        self,
        audio_input: Union[AudioSegment, str],
        model_name: str
    ) -> str:
        """
        Transcrit un audio (local ou API selon la config)
        
        Args:
            audio_input: Audio à transcrire
            model_name: Nom du modèle à utiliser
            
        Returns:
            Texte transcrit
        """
        return self.transcriber.transcribe(audio_input, model_name)
    
    def is_whisper_model(self, model_name: str) -> bool:
        """Vérifie si le modèle est un modèle Whisper local"""
        return self.config.models.is_whisper_model(model_name)
