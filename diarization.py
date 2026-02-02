"""
Service de séparation des locuteurs (diarization) avec NeMo
"""

import os
import json
import re
import logging
from typing import List, Dict, Callable, Optional
from dataclasses import dataclass

from omegaconf import OmegaConf
from nemo.collections.asr.models import ClusteringDiarizer
from pydub import AudioSegment

from exceptions import DiarizationError

logger = logging.getLogger(__name__)


@dataclass
class SpeakerSegment:
    """Segment de parole d'un locuteur"""
    
    speaker: str
    start: float
    end: float
    
    @property
    def duration(self) -> float:
        """Durée du segment en secondes"""
        return self.end - self.start
    
    def to_dict(self) -> Dict:
        """Convertit en dictionnaire"""
        return {
            "speaker": self.speaker,
            "start": self.start,
            "end": self.end
        }


class RTTMParser:
    """Parser pour les fichiers RTTM (diarization output)"""
    
    @staticmethod
    def parse(rttm_path: str) -> List[SpeakerSegment]:
        """
        Parse un fichier RTTM
        
        Format RTTM:
        SPEAKER <file> 1 <start> <duration> <NA> <NA> <speaker> <NA> <NA>
        
        Args:
            rttm_path: Chemin vers le fichier RTTM
            
        Returns:
            Liste de segments de locuteurs
        """
        segments = []
        
        try:
            with open(rttm_path, 'r') as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 8:
                        start = float(parts[3])
                        duration = float(parts[4])
                        speaker = parts[7]
                        
                        segments.append(SpeakerSegment(
                            speaker=speaker,
                            start=start,
                            end=start + duration
                        ))
        
        except Exception as e:
            logger.error(f"Erreur lors du parsing RTTM: {e}")
            raise DiarizationError(f"Échec du parsing RTTM: {e}") from e
        
        return segments


class SegmentMerger:
    """Fusion de segments adjacents du même locuteur"""
    
    def __init__(
        self,
        max_gap: float = 1.2,
        max_duration: float = 30.0,
        min_duration: float = 0.5
    ):
        self.max_gap = max_gap
        self.max_duration = max_duration
        self.min_duration = min_duration
    
    def merge(self, segments: List[SpeakerSegment]) -> List[SpeakerSegment]:
        """
        Fusionne les segments adjacents du même locuteur
        
        Args:
            segments: Liste de segments à fusionner
            
        Returns:
            Liste de segments fusionnés
        """
        if not segments:
            return []
        
        # Trier par temps de début
        sorted_segments = sorted(segments, key=lambda x: x.start)
        merged = []
        current = sorted_segments[0]
        
        for next_seg in sorted_segments[1:]:
            # Conditions de fusion
            same_speaker = next_seg.speaker == current.speaker
            gap = next_seg.start - current.end
            new_duration = next_seg.end - current.start
            
            can_merge = (
                same_speaker and
                gap <= self.max_gap and
                new_duration <= self.max_duration
            )
            
            if can_merge:
                # Fusionner: étendre le segment actuel
                current = SpeakerSegment(
                    speaker=current.speaker,
                    start=current.start,
                    end=next_seg.end
                )
            else:
                # Ne pas fusionner: sauvegarder l'actuel si assez long
                if current.duration >= self.min_duration:
                    merged.append(current)
                current = next_seg
        
        # Ajouter le dernier segment
        if current.duration >= self.min_duration:
            merged.append(current)
        
        return merged


@dataclass
class MultiSpeakerChunk:
    """Chunk audio pouvant contenir plusieurs locuteurs"""
    
    start: float
    end: float
    subsegments: List[SpeakerSegment]
    
    @property
    def duration(self) -> float:
        """Durée totale du chunk"""
        return self.end - self.start


class ChunkBuilder:
    """Construction de chunks multi-locuteurs"""
    
    def __init__(self, max_chunk_duration: float = 60.0):
        self.max_chunk_duration = max_chunk_duration
    
    def build(self, segments: List[SpeakerSegment]) -> List[MultiSpeakerChunk]:
        """
        Regroupe les segments en chunks
        
        Args:
            segments: Liste de segments à regrouper
            
        Returns:
            Liste de chunks multi-locuteurs
        """
        if not segments:
            return []
        
        chunks = []
        current_chunk = MultiSpeakerChunk(
            start=segments[0].start,
            end=segments[0].end,
            subsegments=[segments[0]]
        )
        
        for seg in segments[1:]:
            new_end = seg.end
            duration = new_end - current_chunk.start
            
            if duration <= self.max_chunk_duration:
                # Ajouter au chunk actuel
                current_chunk.end = new_end
                current_chunk.subsegments.append(seg)
            else:
                # Créer un nouveau chunk
                chunks.append(current_chunk)
                current_chunk = MultiSpeakerChunk(
                    start=seg.start,
                    end=seg.end,
                    subsegments=[seg]
                )
        
        # Ajouter le dernier chunk
        chunks.append(current_chunk)
        return chunks


class TextSplitter:
    """Redistribution du texte transcrit vers les locuteurs"""
    
    @staticmethod
    def split_by_speakers(
        text: str,
        segments: List[SpeakerSegment]
    ) -> List[Dict[str, str]]:
        """
        Redistribue le texte vers les segments de locuteurs
        selon la durée relative de chaque segment
        
        Args:
            text: Texte complet transcrit
            segments: Segments de locuteurs
            
        Returns:
            Liste de dict {speaker, text}
        """
        if not text.strip() or not segments:
            return []
        
        total_duration = sum(seg.duration for seg in segments)
        if total_duration <= 0:
            return []
        
        words = text.split()
        total_words = len(words)
        
        results = []
        cursor = 0
        
        for seg in segments:
            # Calculer le nombre de mots pour ce segment
            ratio = seg.duration / total_duration
            word_count = max(1, int(ratio * total_words))
            
            segment_words = words[cursor:cursor + word_count]
            cursor += word_count
            
            if segment_words:
                results.append({
                    "speaker": seg.speaker,
                    "text": " ".join(segment_words)
                })
        
        # Rattacher les mots restants au dernier locuteur
        if cursor < total_words and results:
            results[-1]["text"] += " " + " ".join(words[cursor:])
        
        return results
    
    @staticmethod
    def merge_consecutive_speakers(segments: List[Dict]) -> List[Dict]:
        """
        Fusionne les segments textuels consécutifs du même locuteur
        
        Args:
            segments: Liste de {speaker, text}
            
        Returns:
            Liste fusionnée
        """
        if not segments:
            return []
        
        merged = []
        current = segments[0].copy()
        
        for next_seg in segments[1:]:
            same_speaker = next_seg["speaker"] == current["speaker"]
            ends_sentence = re.search(r"[.!?…]$", current["text"].strip())
            
            if same_speaker and not ends_sentence:
                current["text"] += " " + next_seg["text"]
            else:
                merged.append(current)
                current = next_seg.copy()
        
        merged.append(current)
        return merged


class DiarizationService:
    """Service de diarization avec NeMo"""
    
    def __init__(self, yaml_path: str):
        self.yaml_path = yaml_path
    
    def run(
        self,
        audio_path: str,
        work_dir: str,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> str:
        """
        Exécute la diarization NeMo
        
        Args:
            audio_path: Chemin vers le fichier audio
            work_dir: Répertoire de travail
            progress_callback: Fonction de progression (pct, message)
            
        Returns:
            Chemin du fichier RTTM généré
            
        Raises:
            DiarizationError: En cas d'erreur de diarization
        """
        try:
            os.makedirs(work_dir, exist_ok=True)
            
            self._update_progress(progress_callback, 0.05, "Initialisation de NeMo")
            
            # Créer le manifeste
            manifest_path = os.path.join(work_dir, "manifest.json")
            self._create_manifest(audio_path, manifest_path)
            
            self._update_progress(progress_callback, 0.15, "Chargement de la configuration")
            
            # Configurer NeMo
            cfg = OmegaConf.load(self.yaml_path)
            cfg.diarizer.manifest_filepath = manifest_path
            cfg.diarizer.out_dir = work_dir
            
            self._update_progress(progress_callback, 0.25, "Chargement du modèle NeMo")
            
            # Exécuter la diarization
            diarizer = ClusteringDiarizer(cfg=cfg)
            
            self._update_progress(progress_callback, 0.35, "Détection des voix (VAD)")
            
            diarizer.diarize()
            
            self._update_progress(progress_callback, 0.90, "Finalisation et génération du RTTM")
            
            # Récupérer le fichier RTTM
            rttm_dir = os.path.join(work_dir, "pred_rttms")
            rttm_files = [f for f in os.listdir(rttm_dir) if f.endswith(".rttm")]
            
            if not rttm_files:
                raise DiarizationError("Aucun fichier RTTM généré")
            
            rttm_path = os.path.join(rttm_dir, rttm_files[0])
            
            self._update_progress(progress_callback, 0.99, "Séparation des locuteurs terminée")
            
            return rttm_path
        
        except Exception as e:
            logger.error(f"Erreur lors de la diarization: {e}")
            raise DiarizationError(f"Échec de la diarization: {e}") from e
    
    @staticmethod
    def _create_manifest(audio_path: str, manifest_path: str) -> None:
        """Crée le fichier manifeste pour NeMo"""
        with open(manifest_path, "w") as f:
            json.dump({
                "audio_filepath": audio_path,
                "offset": 0,
                "duration": None,
                "label": "infer",
                "text": "-"
            }, f)
            f.write("\n")
    
    @staticmethod
    def _update_progress(
        callback: Optional[Callable],
        pct: float,
        msg: str
    ) -> None:
        """Met à jour la progression si un callback est fourni"""
        if callback:
            callback(pct, msg)


def extract_audio_segment(
    audio: AudioSegment,
    start: float,
    end: float
) -> AudioSegment:
    """
    Extrait un segment audio
    
    Args:
        audio: AudioSegment complet
        start: Temps de début en secondes
        end: Temps de fin en secondes
        
    Returns:
        Segment audio extrait
    """
    start_ms = int(start * 1000)
    end_ms = int(end * 1000)
    return audio[start_ms:end_ms]
