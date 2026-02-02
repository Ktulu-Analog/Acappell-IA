"""
Configuration centralisée de l'application
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class PathConfig:
    """Configuration des chemins de fichiers"""
    
    diar_yaml: Path
    logo: Optional[Path] = None
    template_docx: Optional[Path] = None
    
    def __post_init__(self):
        self.diar_yaml = Path(self.diar_yaml)
        if self.logo:
            self.logo = Path(self.logo)
        if self.template_docx:
            self.template_docx = Path(self.template_docx)
    
    def validate(self) -> None:
        """Valide l'existence des fichiers requis"""
        if not self.diar_yaml.exists():
            raise FileNotFoundError(f"Fichier de config diarization manquant: {self.diar_yaml}")


@dataclass
class ModelConfig:
    """Configuration des modèles"""
    
    audio_models: List[str]
    text_model: str
    ollama_model: Optional[str] = None
    
    def __post_init__(self):
        self.audio_models = [m.strip() for m in self.audio_models if m.strip()]
    
    @property
    def default_audio_model(self) -> str:
        """Retourne le modèle audio par défaut"""
        return self.audio_models[0] if self.audio_models else ""
    
    def is_whisper_model(self, model_name: str) -> bool:
        """Vérifie si un modèle est un modèle Whisper local"""
        return "whisper" in model_name.lower()


@dataclass
class AudioConfig:
    """Configuration audio"""
    
    language: str = "fr"
    sample_rate: int = 16000
    temperature: float = 0.0
    
    def __post_init__(self):
        if self.sample_rate < 8000 or self.sample_rate > 48000:
            raise ValueError(f"Sample rate invalide: {self.sample_rate}")
        if not 0.0 <= self.temperature <= 1.0:
            raise ValueError(f"Temperature invalide: {self.temperature}")


@dataclass
class APIConfig:
    """Configuration API"""
    
    base_url: str
    api_key: str
    ollama_url: Optional[str] = None
    
    def __post_init__(self):
        if not self.base_url:
            raise ValueError("L'URL de base de l'API est requise")
        if not self.api_key:
            raise ValueError("La clé API est requise")


@dataclass
class AppConfig:
    """Configuration principale de l'application"""
    
    local_mode: bool
    paths: PathConfig
    models: ModelConfig
    audio: AudioConfig
    api: Optional[APIConfig] = None
    
    @classmethod
    def from_env(cls) -> "AppConfig":
        """Charge la configuration depuis les variables d'environnement"""
        local_mode = os.getenv("LOCAL", "NON").upper() == "OUI"
        
        # Modèles
        if local_mode:
            models_raw = os.getenv("WHISPER_LOCAL", "")
        else:
            models_raw = os.getenv("MODEL_AUDIO", "")
        
        audio_models = [m.strip() for m in models_raw.split(",") if m.strip()]
        
        models = ModelConfig(
            audio_models=audio_models,
            text_model=os.getenv("MODEL_TEXT", ""),
            ollama_model=os.getenv("OLLAMA_MODEL", "") if local_mode else None
        )
        
        # Chemins
        paths = PathConfig(
            diar_yaml=os.getenv("DIAR_YAML", "diar_infer.yaml"),
            logo=os.getenv("LOGO_PATH", "logo.png") or None,
            template_docx=os.getenv("TEMPLATE_DOCX", "modelerapport.docx") or None
        )
        
        # Audio
        audio = AudioConfig(
            language=os.getenv("LANGUE", "fr"),
            sample_rate=int(os.getenv("TARGET_SAMPLE_RATE", "16000")),
            temperature=float(os.getenv("TEMPERATURE", "0.0"))
        )
        
        # API
        api = None
        if not local_mode:
            api = APIConfig(
                base_url=os.getenv("BASE-URL", ""),
                api_key=os.getenv("API-KEY", ""),
                ollama_url=None
            )
        else:
            api = APIConfig(
                base_url="",
                api_key="dummy",  # Pas utilisé en mode local
                ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434")
            )
        
        return cls(
            local_mode=local_mode,
            paths=paths,
            models=models,
            audio=audio,
            api=api
        )
    
    def validate(self) -> None:
        """Valide la configuration complète"""
        self.paths.validate()
        
        if not self.models.audio_models:
            raise ValueError("Aucun modèle audio configuré")
        
        if not self.local_mode and not self.api:
            raise ValueError("Configuration API manquante pour le mode API")
        
        if not self.local_mode and not self.api.api_key:
            raise ValueError("Clé API manquante")
