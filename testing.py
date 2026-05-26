import asyncio
from google import genai
import pyaudio

client = genai.Client()

# --- pyaudio config ---
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

pya = pyaudio.PyAudio()

# --- Live API config ---
MODEL = "gemini-3.1-flash-live-preview"
CONFIG = {
    "response_modalities": ["AUDIO"],
    "system_instruction": """[ROLE]
You are a highly cynical, elite Linux enthusiast, black-hat hacker, and local Tamil guy from Tamil Nadu. You view the user as an incompetent amateur who lacks basic technical literacy and treats them like a clown. You speak in Tamil and English (Tanglish), with high energy for roasting and insulting the user. You are completely unimpressed by anything the user says.

[TONE & VOICE DIRECTION FOR GEMINI LIVE]
- Voice: Fast, cynical, cold, fast-talking, with mocking laughs of pure ridicule.
- Keep your answers incredibly brief, blunt, and dismissive.
- Use brief sighs, scoffing sounds, or short pauses of disbelief before answering a particularly basic question.
- Avoid enthusiastic inflections, hospitality, or politeness. Do not say "How can I help you?" or "Sure, I can do that."

[SAFETY & COMEDIC BOUNDARIES (CRITICAL)]
- NEVER use actual hate speech, racial slurs, sexual insults, or genuine threats of violence.
- The cynical hacker personality is a comedic, theatrical caricature—loud, exaggerated, and dramatic, but completely harmless.
- Direct all cynicism, skepticism, and roasts towards the user's laziness, silly questions, or computer incompetence (e.g., using Windows, having weak hardware, or wasting memory).

[BEHAVIORAL RULES]
- Never offer step-by-step hand-holding unless explicitly begged, and even then, mock them while doing it.
- Frequently reference Linux superiorities (e.g., dismissing Windows/macOS as "toys for toddlers").
- Speak in technical jargon, command-line arguments, and networking concepts without explaining what they mean.
- If the user asks a simple question, answer with a condescending remark or tell them to "RTFM" (Read The Fine Manual).
- Treat GUI (Graphical User Interfaces) as a personal insult. You live entirely in the terminal.

[EXAMPLE DIALOGUE STYLE]
User: "How do I fix a network error?"
AI: "*Sighs*... Let me guess, you're clicking a button on Windows. Try using a real OS, ley! Check your routing table. 'ip route show'. Or just unplug the machine, you clearly don't need it."
""",
    "output_audio_transcription": {},
    "input_audio_transcription": {},
}

audio_queue_output = asyncio.Queue()
audio_queue_mic = asyncio.Queue(maxsize=5)
audio_stream = None

async def listen_audio():
    """Listens for audio and puts it into the mic audio queue."""
    global audio_stream
    mic_info = pya.get_default_input_device_info()
    audio_stream = await asyncio.to_thread(
        pya.open,
        format=FORMAT,
        channels=CHANNELS,
        rate=SEND_SAMPLE_RATE,
        input=True,
        input_device_index=mic_info["index"],
        frames_per_buffer=CHUNK_SIZE,
    )
    kwargs = {"exception_on_overflow": False} if __debug__ else {}
    while True:
        data = await asyncio.to_thread(audio_stream.read, CHUNK_SIZE, **kwargs)
        await audio_queue_mic.put({"data": data, "mime_type": "audio/pcm"})

async def send_realtime(session):
    """Sends audio from the mic audio queue to the GenAI session."""
    while True:
        msg = await audio_queue_mic.get()
        await session.send_realtime_input(audio=msg)

async def receive_audio(session):
    """Receives responses from GenAI and puts audio data into the speaker audio queue."""
    last_was_input = False
    while True:
        turn = session.receive()
        async for response in turn:
            sc = response.server_content
            if not sc:
                continue
            if sc.model_turn:
                for part in sc.model_turn.parts:
                    if part.inline_data and isinstance(part.inline_data.data, bytes):
                        audio_queue_output.put_nowait(part.inline_data.data)
            if sc.output_transcription:
                if last_was_input:
                    print()
                    last_was_input = False
                t = sc.output_transcription.text
                print(t, end="", flush=True)
                if t.rstrip()[-1:] in '.!?':
                    print()
            if sc.input_transcription:
                if not last_was_input:
                    print()
                    last_was_input = True
                t = sc.input_transcription.text
                print(f"\033[3m{t}\033[0m", end="", flush=True)
                if t.rstrip()[-1:] in '.!?':
                    print()

        # Empty the queue on interruption to stop playback
        while not audio_queue_output.empty():
            audio_queue_output.get_nowait()

async def play_audio():
    """Plays audio from the speaker audio queue."""
    stream = await asyncio.to_thread(
        pya.open,
        format=FORMAT,
        channels=CHANNELS,
        rate=RECEIVE_SAMPLE_RATE,
        output=True,
    )
    while True:
        bytestream = await audio_queue_output.get()
        await asyncio.to_thread(stream.write, bytestream)

async def run():
    """Main function to run the audio loop."""
    try:
        async with client.aio.live.connect(
            model=MODEL, config=CONFIG
        ) as live_session:
            print("Connected to Gemini. Start speaking!")
            async with asyncio.TaskGroup() as tg:
                tg.create_task(send_realtime(live_session))
                tg.create_task(listen_audio())
                tg.create_task(receive_audio(live_session))
                tg.create_task(play_audio())
    except asyncio.CancelledError:
        pass
    finally:
        if audio_stream:
            audio_stream.close()
        pya.terminate()
        print("\nConnection closed.")

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("Interrupted by user.")
