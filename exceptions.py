"""
Exceptions personnalisées pour l'application
"""


class TranscriptionError(Exception):
    """Erreur de base pour la transcription"""
    pass


class DailyQuotaExceededError(TranscriptionError):
    """Le quota journalier de l'API a été dépassé"""
    pass


class AudioProcessingError(TranscriptionError):
    """Erreur lors du traitement audio"""
    pass


class DiarizationError(Exception):
    """Erreur lors de la séparation des locuteurs"""
    pass


class SummaryGenerationError(Exception):
    """Erreur lors de la génération de synthèse"""
    pass


class DocumentGenerationError(Exception):
    """Erreur lors de la génération de documents"""
    pass


class ConfigurationError(Exception):
    """Erreur de configuration"""
    pass
