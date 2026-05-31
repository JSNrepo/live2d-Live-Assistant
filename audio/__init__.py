"""Audio pipeline, streaming loops, and background tasks."""

from audio.pipeline import (
    mic_q,
    spk_q,
    session_send_q,
    text_input_q,
    mic_reader,
    send_audio,
    recv_audio,
    play_audio,
    session_sender,
    safe_send_realtime_input,
    flush_audio_stream,
    safe_create_task,
    reset_audio_queues,
    text_input_sender,
)

from audio.tasks import (
    execute_screen_analysis,
    execute_shell_command,
    execute_web_search,
    do_background_graph_ingestion,
    run_browser_task,
    get_or_create_prompt_cache,
)
