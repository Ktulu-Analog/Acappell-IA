"""
Gestionnaire de fichiers temporaires et nettoyage
"""

import os
import shutil
import logging
from typing import List, Optional
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CleanupStats:
    """Statistiques de nettoyage"""
    
    files_deleted: int = 0
    dirs_deleted: int = 0
    bytes_freed: int = 0
    errors: List[str] = field(default_factory=list)
    
    def __str__(self) -> str:
        """Représentation lisible"""
        size_mb = self.bytes_freed / (1024 * 1024)
        return (
            f"Fichiers supprimés: {self.files_deleted}, "
            f"Répertoires supprimés: {self.dirs_deleted}, "
            f"Espace libéré: {size_mb:.2f} MB, "
            f"Erreurs: {len(self.errors)}"
        )


class TempFileManager:
    """Gestionnaire de fichiers temporaires avec context manager"""
    
    def __init__(self):
        self.temp_files: List[Path] = []
        self.temp_dirs: List[Path] = []
    
    def register_file(self, filepath: Path) -> None:
        """Enregistre un fichier temporaire"""
        path = Path(filepath)
        if path.exists() and path.is_file():
            self.temp_files.append(path)
    
    def register_dir(self, dirpath: Path) -> None:
        """Enregistre un répertoire temporaire"""
        path = Path(dirpath)
        if path.exists() and path.is_dir():
            self.temp_dirs.append(path)
    
    def cleanup(self, verbose: bool = True) -> CleanupStats:
        """
        Supprime tous les fichiers et répertoires enregistrés
        
        Args:
            verbose: Afficher les messages de progression
            
        Returns:
            Statistiques de nettoyage
        """
        stats = CleanupStats()
        
        # Supprimer les fichiers
        for filepath in self.temp_files:
            try:
                if filepath.exists():
                    size = filepath.stat().st_size
                    filepath.unlink()
                    stats.files_deleted += 1
                    stats.bytes_freed += size
                    
                    if verbose:
                        logger.info(f"Fichier supprimé: {filepath}")
            
            except Exception as e:
                error_msg = f"Erreur suppression {filepath}: {e}"
                stats.errors.append(error_msg)
                logger.error(error_msg)
        
        # Supprimer les répertoires
        for dirpath in self.temp_dirs:
            try:
                if dirpath.exists():
                    # Calculer la taille avant suppression
                    size = sum(
                        f.stat().st_size
                        for f in dirpath.rglob("*")
                        if f.is_file()
                    )
                    
                    shutil.rmtree(dirpath)
                    stats.dirs_deleted += 1
                    stats.bytes_freed += size
                    
                    if verbose:
                        logger.info(f"Répertoire supprimé: {dirpath}")
            
            except Exception as e:
                error_msg = f"Erreur suppression {dirpath}: {e}"
                stats.errors.append(error_msg)
                logger.error(error_msg)
        
        # Réinitialiser les listes
        self.temp_files.clear()
        self.temp_dirs.clear()
        
        return stats
    
    def __enter__(self):
        """Support du context manager"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Nettoyage automatique à la sortie du contexte"""
        self.cleanup(verbose=False)
        return False


class AudioTempCleaner:
    """Nettoyeur spécialisé pour les fichiers audio temporaires"""
    
    def __init__(self, audio_stem: str):
        self.audio_stem = audio_stem
    
    def get_temp_files(self) -> List[Path]:
        """Retourne la liste des fichiers temporaires pour cet audio"""
        patterns = [
            f"{self.audio_stem}_normalized.wav",
            f"{self.audio_stem}.wav",
            f"{self.audio_stem}.mp3",
        ]
        
        return [Path(p) for p in patterns if Path(p).exists()]
    
    def get_temp_dirs(self) -> List[Path]:
        """Retourne la liste des répertoires temporaires pour cet audio"""
        diar_dir = Path(f"diar_{self.audio_stem}")
        return [diar_dir] if diar_dir.exists() else []
    
    def cleanup(self, verbose: bool = True) -> CleanupStats:
        """
        Nettoie tous les fichiers temporaires liés à cet audio
        
        Args:
            verbose: Afficher les messages
            
        Returns:
            Statistiques de nettoyage
        """
        stats = CleanupStats()
        
        # Nettoyer les fichiers
        for filepath in self.get_temp_files():
            try:
                size = filepath.stat().st_size
                filepath.unlink()
                stats.files_deleted += 1
                stats.bytes_freed += size
                
                if verbose:
                    logger.info(f"Fichier supprimé: {filepath}")
            
            except Exception as e:
                error_msg = f"Erreur suppression {filepath}: {e}"
                stats.errors.append(error_msg)
                logger.error(error_msg)
        
        # Nettoyer les répertoires
        for dirpath in self.get_temp_dirs():
            try:
                size = sum(
                    f.stat().st_size
                    for f in dirpath.rglob("*")
                    if f.is_file()
                )
                
                shutil.rmtree(dirpath)
                stats.dirs_deleted += 1
                stats.bytes_freed += size
                
                if verbose:
                    logger.info(f"Répertoire supprimé: {dirpath}")
            
            except Exception as e:
                error_msg = f"Erreur suppression {dirpath}: {e}"
                stats.errors.append(error_msg)
                logger.error(error_msg)
        
        return stats


class GlobalTempCleaner:
    """Nettoyeur global de tous les fichiers temporaires"""
    
    @staticmethod
    def find_all_temp_files() -> List[Path]:
        """Trouve tous les fichiers temporaires"""
        return list(Path(".").glob("*_normalized.wav"))
    
    @staticmethod
    def find_all_temp_dirs() -> List[Path]:
        """Trouve tous les répertoires temporaires"""
        return [d for d in Path(".").glob("diar_*") if d.is_dir()]
    
    @classmethod
    def cleanup(cls, verbose: bool = True) -> CleanupStats:
        """
        Nettoie TOUS les fichiers temporaires
        
        Args:
            verbose: Afficher les messages
            
        Returns:
            Statistiques de nettoyage
        """
        stats = CleanupStats()
        
        # Nettoyer les fichiers
        for filepath in cls.find_all_temp_files():
            try:
                size = filepath.stat().st_size
                filepath.unlink()
                stats.files_deleted += 1
                stats.bytes_freed += size
                
                if verbose:
                    logger.info(f"Fichier supprimé: {filepath}")
            
            except Exception as e:
                error_msg = f"Erreur suppression {filepath}: {e}"
                stats.errors.append(error_msg)
                logger.error(error_msg)
        
        # Nettoyer les répertoires
        for dirpath in cls.find_all_temp_dirs():
            try:
                size = sum(
                    f.stat().st_size
                    for f in dirpath.rglob("*")
                    if f.is_file()
                )
                
                shutil.rmtree(dirpath)
                stats.dirs_deleted += 1
                stats.bytes_freed += size
                
                if verbose:
                    logger.info(f"Répertoire supprimé: {dirpath}")
            
            except Exception as e:
                error_msg = f"Erreur suppression {dirpath}: {e}"
                stats.errors.append(error_msg)
                logger.error(error_msg)
        
        return stats


def get_temp_files_size(audio_stem: Optional[str] = None) -> dict:
    """
    Calcule la taille des fichiers temporaires
    
    Args:
        audio_stem: Si fourni, calcule uniquement pour ce fichier
        
    Returns:
        Dict avec tailles en bytes: {files, dirs, total}
    """
    sizes = {"files": 0, "dirs": 0, "total": 0}
    
    if audio_stem:
        # Taille pour un fichier spécifique
        cleaner = AudioTempCleaner(audio_stem)
        
        for filepath in cleaner.get_temp_files():
            if filepath.exists():
                sizes["files"] += filepath.stat().st_size
        
        for dirpath in cleaner.get_temp_dirs():
            if dirpath.exists():
                sizes["dirs"] += sum(
                    f.stat().st_size
                    for f in dirpath.rglob("*")
                    if f.is_file()
                )
    else:
        # Taille totale
        for filepath in GlobalTempCleaner.find_all_temp_files():
            if filepath.exists():
                sizes["files"] += filepath.stat().st_size
        
        for dirpath in GlobalTempCleaner.find_all_temp_dirs():
            if dirpath.exists():
                sizes["dirs"] += sum(
                    f.stat().st_size
                    for f in dirpath.rglob("*")
                    if f.is_file()
                )
    
    sizes["total"] = sizes["files"] + sizes["dirs"]
    return sizes


def format_size(size_bytes: int) -> str:
    """
    Formate une taille en bytes vers un format lisible
    
    Args:
        size_bytes: Taille en bytes
        
    Returns:
        Taille formatée (ex: "15.4 MB")
    """
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"
