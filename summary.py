"""
Service de génération de synthèses (comptes rendus)
"""

import yaml
import logging
from typing import Dict, Callable, Optional
from pathlib import Path

import requests
from openai import OpenAI
import docx
import PyPDF2

from config import AppConfig
from exceptions import SummaryGenerationError

logger = logging.getLogger(__name__)


class SummaryStyleLoader:
    """Chargement des styles de synthèse depuis YAML"""
    
    def __init__(self, yaml_path: str = "summary_styles.yaml"):
        self.yaml_path = Path(yaml_path)
        self._styles = None
    
    @property
    def styles(self) -> Dict:
        """Charge et retourne les styles (avec cache)"""
        if self._styles is None:
            self._styles = self._load_styles()
        return self._styles
    
    def _load_styles(self) -> Dict:
        """Charge les styles depuis le fichier YAML"""
        try:
            with open(self.yaml_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            return data.get("summary_styles", {})
        except Exception as e:
            logger.error(f"Erreur lors du chargement des styles: {e}")
            return {}
    
    def get_style(self, style_name: str) -> Optional[Dict]:
        """Récupère un style spécifique"""
        return self.styles.get(style_name)


class FileTextExtractor:
    """Extraction de texte depuis différents formats de fichiers"""
    
    @staticmethod
    def extract(uploaded_file) -> str:
        """
        Extrait le texte d'un fichier uploadé
        
        Formats supportés: TXT, DOCX, PDF
        
        Args:
            uploaded_file: Fichier Streamlit uploadé
            
        Returns:
            Texte extrait
            
        Raises:
            ValueError: Format non supporté
            RuntimeError: Erreur d'extraction
        """
        file_extension = uploaded_file.name.lower().split(".")[-1]
        
        try:
            if file_extension == "txt":
                return uploaded_file.read().decode("utf-8")
            
            elif file_extension == "docx":
                doc = docx.Document(uploaded_file)
                return "\n".join([p.text for p in doc.paragraphs])
            
            elif file_extension == "pdf":
                pdf_reader = PyPDF2.PdfReader(uploaded_file)
                text = ""
                for page in pdf_reader.pages:
                    text += page.extract_text() + "\n"
                return text
            
            else:
                raise ValueError(f"Format non supporté: {file_extension}")
        
        except Exception as e:
            raise RuntimeError(f"Erreur lors de l'extraction du texte: {e}") from e


class PromptBuilder:
    """Construction de prompts pour la génération de synthèses"""
    
    @staticmethod
    def build(
        style_config: Dict,
        text: str,
        agenda_text: Optional[str] = None
    ) -> str:
        """
        Construit un prompt de synthèse
        
        Args:
            style_config: Configuration du style de synthèse
            text: Texte à synthétiser
            agenda_text: Ordre du jour (optionnel)
            
        Returns:
            Prompt complet
        """
        prompt_parts = [
            style_config["prompt"]["intro"],
            "",
            style_config["description"],
            ""
        ]
        
        # Ajouter l'ordre du jour si fourni
        if agenda_text:
            prompt_parts.extend([
                "ORDRE DU JOUR DE LA RÉUNION :",
                "---",
                agenda_text,
                "---",
                "",
                "Tu dois IMPÉRATIVEMENT suivre cet ordre du jour pour structurer le compte rendu.",
                "Chaque point de l'ordre du jour doit apparaître comme section dans le compte rendu.",
                "Si un point n'a pas été abordé dans la transcription, indique-le clairement.",
                ""
            ])
        
        # Contraintes
        prompt_parts.append("Contraintes STRICTES :")
        for constraint in style_config["prompt"]["constraints"]:
            prompt_parts.append(f"- {constraint}")
        
        # Règles
        prompt_parts.append("\nRègles spéciales :")
        for rule in style_config["prompt"]["rules"]:
            prompt_parts.append(f"- {rule}")
        
        # Format et texte
        prompt_parts.extend([
            "\nFormat attendu :",
            style_config["prompt"]["format"],
            "",
            style_config["prompt"]["text_to_summarize"],
            text
        ])
        
        return "\n".join(prompt_parts)


class OllamaSummaryGenerator:
    """Générateur de synthèses avec Ollama (local)"""
    
    def __init__(self, ollama_url: str, model_name: str):
        self.ollama_url = ollama_url
        self.model_name = model_name
    
    def generate(self, prompt: str) -> str:
        """
        Génère une synthèse avec Ollama
        
        Args:
            prompt: Prompt de synthèse
            
        Returns:
            Synthèse générée
            
        Raises:
            SummaryGenerationError: En cas d'erreur
        """
        try:
            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.model_name,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Tu es un assistant expert en synthèse de réunions."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    "stream": False,
                    "options": {"temperature": 0.2}
                },
                timeout=300
            )
            response.raise_for_status()
            
            return response.json().get("message", {}).get("content", "").strip()
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur Ollama: {e}")
            raise SummaryGenerationError(f"Échec de la génération avec Ollama: {e}") from e


class APISummaryGenerator:
    """Générateur de synthèses via API OpenAI"""
    
    def __init__(self, client: OpenAI, model_name: str):
        self.client = client
        self.model_name = model_name
    
    def generate(self, prompt: str) -> str:
        """
        Génère une synthèse via l'API
        
        Args:
            prompt: Prompt de synthèse
            
        Returns:
            Synthèse générée
            
        Raises:
            SummaryGenerationError: En cas d'erreur
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "Tu es un assistant expert en synthèse de réunions."
                    },
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2
            )
            
            return response.choices[0].message.content.strip()
        
        except Exception as e:
            logger.error(f"Erreur API: {e}")
            raise SummaryGenerationError(f"Échec de la génération via API: {e}") from e


class SummaryService:
    """Service principal de génération de synthèses"""
    
    def __init__(
        self,
        config: AppConfig,
        client: Optional[OpenAI] = None,
        styles_path: str = "summary_styles.yaml"
    ):
        self.config = config
        self.style_loader = SummaryStyleLoader(styles_path)
        
        # Initialiser le générateur approprié
        if config.local_mode:
            self.generator = OllamaSummaryGenerator(
                ollama_url=config.api.ollama_url,
                model_name=config.models.ollama_model
            )
        else:
            if not client:
                raise ValueError("Client OpenAI requis pour le mode API")
            self.generator = APISummaryGenerator(
                client=client,
                model_name=config.models.text_model
            )
    
    @property
    def styles(self) -> Dict:
        """Retourne les styles disponibles"""
        return self.style_loader.styles
    
    def generate(
        self,
        text: str,
        style: str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        agenda_text: Optional[str] = None
    ) -> str:
        """
        Génère une synthèse selon le style demandé
        
        Args:
            text: Texte à synthétiser
            style: Style de synthèse
            progress_callback: Callback (pct, message)
            agenda_text: Ordre du jour (optionnel)
            
        Returns:
            Synthèse générée
            
        Raises:
            SummaryGenerationError: En cas d'erreur
        """
        self._update_progress(progress_callback, 0, "Préparation de la synthèse…")
        
        if not text.strip():
            self._update_progress(progress_callback, 100, "Aucune donnée exploitable ❌")
            return "Aucune donnée exploitable pour la synthèse."
        
        # Récupérer la config du style
        style_config = self.style_loader.get_style(style)
        if not style_config:
            raise SummaryGenerationError(f"Style '{style}' non trouvé")
        
        self._update_progress(progress_callback, 20, "Analyse du contenu…")
        
        # Construire le prompt
        prompt = PromptBuilder.build(style_config, text, agenda_text)
        
        self._update_progress(progress_callback, 60, "Génération de la synthèse…")
        
        # Générer la synthèse
        summary = self.generator.generate(prompt)
        
        self._update_progress(progress_callback, 100, "Synthèse terminée ✅")
        
        # Validation
        if not summary or not summary.strip():
            logger.warning("Synthèse vide générée")
            return "Synthèse vide générée. Veuillez réessayer."
        
        logger.info(f"Synthèse générée: {len(summary)} caractères")
        return summary
    
    @staticmethod
    def _update_progress(
        callback: Optional[Callable],
        pct: int,
        msg: str
    ) -> None:
        """Met à jour la progression si un callback est fourni"""
        if callback:
            callback(pct, msg)
