# =============================================================================
# Transcription audio avec séparation des locuteurs et génération de synthèse.
# Ce programme a 2 modes de fonctionnement :
# Local, avec une instance Ollama fournissant les modèles
# API, avec une connexion à l'API Albert
#
# Seule la séparation des locuteurs s'effectue en local dans les 2 cas.
# Elle utilise le modèle Nemo sur GPU ou fallback sur CPU (plus lent)
# =============================================================================

import os
import pathlib
import logging
from typing import Dict, Optional

import streamlit as st
from pydub import AudioSegment
from openai import OpenAI

from config import AppConfig
from transcription import TranscriptionService
from diarization import (
    DiarizationService,
    RTTMParser,
    SegmentMerger,
    ChunkBuilder,
    TextSplitter,
    extract_audio_segment,
)
from summary import SummaryService, FileTextExtractor
from cleanup import TempFileManager, AudioTempCleaner, GlobalTempCleaner, get_temp_files_size, format_size
from exceptions import DailyQuotaExceededError
from docx_utils import build_transcription_doc, build_summary_doc

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# =============================================================================
# Fonctions de l'interface utilisateur
# =============================================================================

def init_session_state():
    """Initialise l'état de la session Streamlit"""
    defaults = {
        "transcription_done": False,
        "docx_transcription": None,
        "syntheses": {},
        "final_segments": [],
        "audio_stem": None,
        "uploaded_name": None,
        "processing": False,
        "uploaded_audio": None,
        "audio_model": None,
        "agenda_text": None,
        "agenda_filename": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def init_services(config: AppConfig):
    """Initialise les services nécessaires"""
    client = None
    if not config.local_mode:
        client = OpenAI(base_url=config.api.base_url, api_key=config.api.api_key)

    return {
        "transcription": TranscriptionService(config, client),
        "diarization": DiarizationService(str(config.paths.diar_yaml)),
        "summary": SummaryService(config, client),
    }


def display_header(config: AppConfig):
    """Affiche l'en-tête de l'application"""
    if config.paths.logo and config.paths.logo.exists():
        st.image(str(config.paths.logo))
    st.markdown("vos infos ici (et un logo au dessus si logo.png présent)")
    st.title("A cappell*IA, la transcription audio")
    mode_text = "🖥️ Mode local avec Ollama" if config.local_mode else "☁️ Mode Albert API"
    st.info(f"{mode_text} activé")

    # Afficher les infos sur les fichiers temporaires
    temp_sizes = get_temp_files_size()
    if temp_sizes["total"] > 0:
        with st.expander("🗑️ Fichiers temporaires"):
            st.write(f"Espace utilisé : **{format_size(temp_sizes['total'])}**")
            st.write(f"- Fichiers : {format_size(temp_sizes['files'])}")
            st.write(f"- Répertoires : {format_size(temp_sizes['dirs'])}")

            if st.button("🗑️ Nettoyer tous les fichiers temporaires"):
                with st.spinner("Nettoyage en cours..."):
                    stats = GlobalTempCleaner.cleanup(verbose=False)
                    st.success(
                        f"✅ Nettoyage terminé ! {stats.files_deleted} fichiers et "
                        f"{stats.dirs_deleted} répertoires supprimés."
                    )
                    st.rerun()


def prepare_audio(uploaded_file, config: AppConfig):
    """Prépare et normalise le fichier audio"""
    audio_path = uploaded_file.name
    with open(audio_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    audio = AudioSegment.from_file(audio_path)
    audio = audio.set_channels(1).set_frame_rate(config.audio.sample_rate)

    stem = pathlib.Path(uploaded_file.name).stem
    normalized_path = f"{stem}_normalized.wav"
    audio.export(normalized_path, format="wav")

    return audio, normalized_path


def make_progress_callback(initial_text="Traitement en cours…"):
    """Crée un callback de progression pour Streamlit"""
    progress_bar = st.progress(0, text=initial_text)
    status = st.empty()

    def callback(pct, message=None):
        progress_bar.progress(int(pct * 100))
        if message:
            status.text(message)

    return callback


def get_main_speaker(chunk):
    """Identifie le locuteur principal d'un chunk"""
    speaker_durations = {}
    for subseg in chunk.subsegments:
        speaker = subseg.speaker
        duration = subseg.duration
        speaker_durations[speaker] = speaker_durations.get(speaker, 0) + duration

    main_speaker = max(speaker_durations, key=speaker_durations.get)
    return main_speaker


def run_transcription_pipeline(
    audio, normalized_path, audio_stem, chunk_duration, audio_model, services, config
):
    """Exécute le pipeline complet de transcription"""

    st.subheader("Étape 1/3 : Séparation des locuteurs")
    progress_cb = make_progress_callback("Diarization NeMo en cours…")
    diar_dir = f"./diar_{audio_stem}"
    rttm_path = services["diarization"].run(
        normalized_path, diar_dir, progress_callback=progress_cb
    )

    st.subheader("Étape 2/3 : Préparation des segments audio")
    speaker_segments = RTTMParser.parse(rttm_path)

    # Fusionner les segments
    merger = SegmentMerger()
    merged_segments = merger.merge(speaker_segments)

    # Construire les chunks
    chunk_builder = ChunkBuilder(max_chunk_duration=chunk_duration)
    chunks = chunk_builder.build(merged_segments)

    st.info(f"Nombre d'appels vers l'API estimés : {len(chunks)}")

    st.subheader("Étape 3/3 : Transcription du fichier audio")
    progress_bar = st.progress(0, text="Transcription en cours…")
    status = st.empty()
    final_segments = []
    total_chunks = len(chunks)

    try:
        for i, chunk in enumerate(chunks, start=1):
            main_speaker = get_main_speaker(chunk)
            status.text(
                f"Transcription du chunk {i}/{total_chunks} - Locuteur principal : {main_speaker}"
            )
            audio_chunk = extract_audio_segment(audio, chunk.start, chunk.end)

            if len(audio_chunk) < 1000:
                continue

            text = services["transcription"].transcribe(audio_chunk, audio_model)

            if not text:
                continue

            # Redistribuer le texte vers les locuteurs
            distributed = TextSplitter.split_by_speakers(text, chunk.subsegments)
            final_segments.extend(distributed)

            progress_bar.progress(int(i / total_chunks * 100))

        # Fusionner les segments consécutifs du même locuteur
        final_segments = TextSplitter.merge_consecutive_speakers(final_segments)

        progress_bar.progress(100)
        status.text("Transcription terminée ✅")

        # Nettoyage des fichiers temporaires
        st.info("🗑️ Nettoyage des fichiers temporaires...")
        cleaner = AudioTempCleaner(audio_stem)
        cleaner.cleanup(verbose=False)

        return final_segments

    except DailyQuotaExceededError:
        st.error(
            "⚠️ Quota journalier API Albert atteint pour votre compte.\n\n"
            "Merci de réessayer demain."
        )
        # Nettoyer même en cas d'erreur
        cleaner = AudioTempCleaner(audio_stem)
        cleaner.cleanup(verbose=False)
        st.stop()
    except Exception as e:
        # Nettoyer même en cas d'erreur
        cleaner = AudioTempCleaner(audio_stem)
        cleaner.cleanup(verbose=False)
        raise


def save_transcription(segments, filename, audio_stem):
    """Sauvegarde la transcription dans un document Word"""
    doc = build_transcription_doc(segments, filename)
    output_path = f"{audio_stem}_transcription.docx"
    doc.save(output_path)
    return output_path


def handle_agenda_upload():
    """Gère le téléchargement de l'ordre du jour"""
    uploaded_agenda = st.file_uploader(
        "📋 Téléchargez l'ordre du jour (optionnel)",
        type=["txt", "docx", "pdf"],
        key="agenda_uploader",
        help="L'ordre du jour sera utilisé pour structurer le compte rendu",
    )

    if uploaded_agenda:
        try:
            agenda_text = FileTextExtractor.extract(uploaded_agenda)
            st.session_state.agenda_text = agenda_text
            st.session_state.agenda_filename = uploaded_agenda.name
            st.success(f"✅ Ordre du jour chargé : {uploaded_agenda.name}")

            with st.expander("👀 Aperçu de l'ordre du jour"):
                st.text(agenda_text[:500] + "..." if len(agenda_text) > 500 else agenda_text)
        except Exception as e:
            st.error(f"❌ Erreur lors du chargement : {e}")


# =============================================================================
# Interface principale
# =============================================================================

def main():
    """Point d'entrée principal de l'application"""
    st.set_page_config(
        page_title="A cappell*IA",
        page_icon="🎙️",
        layout="wide",
    )

    # Charger la configuration
    try:
        config = AppConfig.from_env()
        config.validate()
    except Exception as e:
        st.error(f"❌ Erreur de configuration : {e}")
        st.stop()

    # Initialiser
    init_session_state()
    services = init_services(config)

    # Affichage
    display_header(config)

    # === ÉTAPE 1 : Upload ===
    st.markdown("---")
    st.markdown("### Téléchargement du fichier audio")

    uploaded_audio = st.file_uploader(
        "Choisissez un fichier audio",
        type=["wav", "mp3", "m4a", "ogg", "flac"],
        help="Formats supportés : WAV, MP3, M4A, OGG, FLAC",
    )

    if not uploaded_audio:
        with st.expander("ℹ️ Guide d'utilisation", expanded=True):
            st.markdown(
                """
            ### Comment utiliser cette application ?

            1. **Téléchargez** votre fichier audio
            2. **Sélectionnez** le modèle de transcription
            3. **Configurez** les paramètres (durée des chunks)
            4. **Lancez** la transcription

            ### Fonctionnalités
            - Transcription audio automatique
            - Séparation des locuteurs (diarization)
            - Génération de comptes rendus
            - Compte rendu structuré selon ordre du jour
            - Export au format Word
            """
            )
        return

    # Infos fichier
    file_size = uploaded_audio.size / (1024 * 1024)
    st.success(f"✅ Fichier chargé : **{uploaded_audio.name}** ({file_size:.2f} MB)")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Format", pathlib.Path(uploaded_audio.name).suffix.upper())
    with col2:
        st.metric("Taille", f"{file_size:.2f} MB")
    with col3:
        estimated_min = int(file_size * 1.2)
        st.metric("Durée estimée", f"~{estimated_min} min")

    # === ÉTAPE 2 : Modèle ===
    st.markdown("---")
    st.markdown("### Choix du modèle de transcription")
    selected_model = st.radio(
        "Sélectionnez le modèle de transcription",
        options=config.models.audio_models,
        index=0,
        help="Plus le modèle est grand, plus la transcription est précise.",
    )
    st.session_state.audio_model = selected_model

    # === ÉTAPE 3 : Paramètres ===
    st.markdown("---")
    st.markdown("### Configuration")

    if config.models.is_whisper_model(selected_model):
        chunk_duration = 30
        st.info("⚠️ Chunks fixés à 30 secondes pour Whisper")
    else:
        chunk_duration = st.slider("Durée max des chunks (sec)", 10, 600, 60, 5)

    # === ÉTAPE 4 : Lancement ===
    st.markdown("---")
    st.markdown("### Lancement de la transcription")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        start_button = st.button(
            "🎬 Démarrer la transcription",
            disabled=st.session_state.processing,
            use_container_width=True,
            type="primary",
        )

    # === PIPELINE ===
    if start_button and not st.session_state.transcription_done:
        st.session_state.processing = True
        st.session_state.uploaded_audio = uploaded_audio
        st.session_state.uploaded_name = uploaded_audio.name
        st.session_state.audio_stem = pathlib.Path(uploaded_audio.name).stem

        st.markdown("---")
        st.markdown("### Traitement en cours")

        audio, normalized_path = prepare_audio(uploaded_audio, config)
        final_segments = run_transcription_pipeline(
            audio,
            normalized_path,
            st.session_state.audio_stem,
            chunk_duration,
            selected_model,
            services,
            config,
        )

        output_path = save_transcription(
            final_segments, st.session_state.uploaded_name, st.session_state.audio_stem
        )

        st.session_state.final_segments = final_segments
        st.session_state.docx_transcription = output_path
        st.session_state.transcription_done = True
        st.session_state.processing = False
        st.balloons()

    # === RÉSULTATS ===
    if st.session_state.docx_transcription:
        st.markdown("---")
        st.markdown("### Transcription terminée !")

        col1, col2 = st.columns([2, 1])
        with col1:
            st.success(
                f"📄 La transcription de **{st.session_state.uploaded_name}** est prête !\n\n"
                f"🗣️ Nombre de segments : {len(st.session_state.final_segments)}"
            )
        with col2:
            with open(st.session_state.docx_transcription, "rb") as f:
                st.download_button(
                    "📥 Télécharger",
                    f,
                    file_name=st.session_state.docx_transcription,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                    type="primary",
                )

        with st.expander("👀 Aperçu de la transcription", expanded=True):
            preview = st.session_state.final_segments[:5]
            for seg in preview:
                st.markdown(f"**{seg['speaker']}** : {seg['text']}")
            if len(st.session_state.final_segments) > 5:
                st.markdown(
                    f"*... et {len(st.session_state.final_segments) - 5} segments supplémentaires*"
                )

        # === SYNTHÈSE ===
        st.markdown("---")
        st.markdown("### Génération du compte rendu (optionnel)")

        summary_styles = services["summary"].styles
        col1, col2 = st.columns([2, 1])

        with col1:
            selected_style = st.selectbox(
                "Style de synthèse",
                list(summary_styles.keys()),
                format_func=lambda x: summary_styles[x]["label"],
            )
            if selected_style:
                st.caption(summary_styles[selected_style]["description"])

        # Upload de l'ordre du jour si nécessaire
        if selected_style == "ordre_du_jour":
            st.markdown("---")
            handle_agenda_upload()

        with col2:
            generate_disabled = (
                selected_style == "ordre_du_jour" and not st.session_state.agenda_text
            )
            generate_btn = st.button(
                "✨ Générer",
                use_container_width=True,
                type="secondary",
                disabled=generate_disabled,
            )

        if generate_disabled and selected_style == "ordre_du_jour":
            st.warning(
                "⚠️ Veuillez télécharger un ordre du jour pour ce type de compte rendu"
            )

        if generate_btn:
            full_text = "\n".join(
                f"{s['speaker']} : {s['text']}" for s in st.session_state.final_segments
            )

            with st.spinner(f"Génération ({selected_style})..."):

                def progress_cb(pct, msg):
                    if not hasattr(progress_cb, "bar"):
                        progress_cb.bar = st.progress(0)
                        progress_cb.status = st.empty()
                    progress_cb.bar.progress(pct)
                    progress_cb.status.text(msg)

                # Passer l'ordre du jour si nécessaire
                agenda_to_use = (
                    st.session_state.agenda_text
                    if selected_style == "ordre_du_jour"
                    else None
                )

                summary_text = services["summary"].generate(
                    full_text,
                    selected_style,
                    progress_callback=progress_cb,
                    agenda_text=agenda_to_use,
                )
                doc = build_summary_doc(summary_text, st.session_state.uploaded_name)
                safe_style = selected_style.lower().replace(" ", "_")
                output_path = (
                    f"{safe_style}_{st.session_state.audio_stem}_synthese.docx"
                )
                doc.save(output_path)
                st.session_state.syntheses[selected_style] = output_path
                st.success("✅ Compte rendu généré !")

        # Téléchargements synthèses
        if st.session_state.syntheses:
            st.markdown("### Comptes rendus disponibles")
            for style, path in st.session_state.syntheses.items():
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.write(f"📄 {summary_styles[style]['label']}")
                with col2:
                    with open(path, "rb") as f:
                        st.download_button(
                            "📥",
                            f,
                            file_name=path,
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key=f"dl_{style}",
                            use_container_width=True,
                        )

        # Reset
        st.markdown("---")
        if st.button("🔄 Nouvelle transcription", type="secondary"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


# =============================================================================
# Point d'entrée
# =============================================================================

if __name__ == "__main__":
    main()
