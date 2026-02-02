# Acappell-IA
Application de transcription audio avec séparation des locuteurs et génération automatique de comptes rendus. Supporte deux modes de fonctionnement :
- **Mode Local** : Utilise Whisper et Ollama en local
- **Mode API** : Utilise l'API Albert de la DiNum pour la transcription et la génération de texte

## Installation

### Prérequis

- Python 3.9+ (testé avec 3.12.8)
- CUDA (optionnel, pour GPU)
- Ollama (si mode local)
- Clé API Albert

### Installation des dépendances

```bash
pip install -r requirements.txt
```

### Configuration

Créez un fichier `.env` à la racine du projet sur le modèle de .env-exemple.


## Utilisation

### Lancement de l'application

```bash
streamlit run app.py
