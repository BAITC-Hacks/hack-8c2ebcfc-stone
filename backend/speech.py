"""Audio transport. Transcription preserves RU/KK code-switching verbatim."""
import os
import time

from backend.router import _get_client

STT_PROMPT = (
    "Сақтандыру компаниясының байланыс орталығы. Қазақша және орысша аралас сөйлеу. "
    "Страховая компания: русский, казахский и смешанная речь. "
    "ОГПО, КАСКО, ДМС, сақтандыру полисі, төлем, төлеу, ұзарту, өтініш, дәрігер. "
    "Страховой полис, оплата, продление, заявление, врач. "
    "Тілді өзгертпей жазыңыз. Записывайте слова на языке оригинала, без перевода."
)


def transcribe(audio_file) -> tuple[str, float]:
    t0 = time.perf_counter()
    response = _get_client().audio.transcriptions.create(
        model=os.getenv("STT_MODEL", "gpt-4o-transcribe"),
        file=("speech.wav", audio_file.getvalue(), "audio/wav"),
        prompt=STT_PROMPT,
        response_format="json",
        timeout=30,
    )
    text = response.text.strip()
    if not text:
        raise ValueError("No speech recognized")
    return text, (time.perf_counter() - t0) * 1000


def speak(text: str, language: str = "ru") -> tuple[bytes, float]:
    t0 = time.perf_counter()
    instructions = (
        "Speak clearly in Kazakh (қазақ тілі), with natural Kazakh pronunciation. "
        "Preserve Russian words and brand names as written. Do not translate the text."
        if language in ("kk", "mixed") else
        "Speak clearly in Russian. Preserve Kazakh words and brand names as written. "
        "Do not translate the text."
    )
    response = _get_client().audio.speech.create(
        model=os.getenv("TTS_MODEL", "gpt-4o-mini-tts"),
        voice=os.getenv("TTS_VOICE", "alloy"),
        input=text,
        instructions=instructions,
        response_format="mp3",
        timeout=30,
    )
    if not response.content:
        raise ValueError("No speech audio returned")
    return response.content, (time.perf_counter() - t0) * 1000
