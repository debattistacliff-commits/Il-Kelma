# How Handy works

Handy (MIT licence) inspired Il-Kelma. This study follows the local Handy source. The file paths below start at Handy's `src-tauri/src/` folder. Il-Kelma uses its own code.

| Step | What Handy does and where | What Il-Kelma should do |
| --- | --- | --- |
| 1. Shortcut | `shortcut/handler.rs` forwards key presses and releases. `transcription_coordinator.rs` decides when to start and stop. In hold-or-tap mode, a short tap keeps it listening. A longer hold ends on release. | Use one clear rule. Press once to start. Press again to stop. Show a big listening light. |
| 2. Microphone | `actions.rs` starts and stops capture through `managers/audio.rs`. The manager uses the chosen mic or falls back to the system default. | Show the mic name and a sound meter. Let Clifford test and save his mic before dictating. |
| 3. Silence filter | `managers/audio.rs` sets up voice activity detection (VAD). `audio_toolkit/vad/silero.rs` scores short chunks for speech. When enabled, `audio_toolkit/audio/sink.rs` keeps speech and drops noise. `audio_toolkit/chunking.rs` finds speech spans for batch decoding. | Ignore quiet gaps and keep a little sound on both sides of speech. Check quiet voices and soft word endings. |
| 4. Speech model | `managers/transcription.rs` finishes a live result or decodes the recording in a batch. `engine_supervisor/supervisor.rs` runs the selected speech engine. | Start with one model and test it on real Maltese, English and Italian recordings. Check Maltese names and everyday speech. |
| 5. Language | `managers/transcription.rs` uses a selected language when the model supports it. With some models, `language_detection/detector.rs` can guess from a short piece of speech before decoding. Other models can return their own guess. A text check can guide clean-up if evidence is missing. | Offer clear language buttons and an Auto option. Use the chosen language to guard spelling fixes. |
| 6. Text clean-up | `managers/transcription.rs` normalises the transcript. It can apply custom words and remove fillers. `actions.rs` can also request a separate rewrite for a post-processing shortcut. | Keep the original words close to the speech. Use the Maltese word list for clear spelling fixes. Let Clifford hear the result first. |
| 7. Paste | `actions.rs` hands the final text to `clipboard.rs`. A common method puts the text on the clipboard and sends a paste shortcut to the active app. | Show the target app. If focus changes, pause and let Clifford choose where to send the text. |

## Problems Clifford hit

- The system picked a quiet Kinect mic. A mic test and sound meter would show the problem early.
- Hold-or-tap was confusing. One tap to start and one tap to stop would make it clear.
- Paste went to the wrong window. A named target and a final focus check would help.
