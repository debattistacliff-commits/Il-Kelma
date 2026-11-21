# Il-Kelma for builders

Start Il-Kelma. The API listens on `127.0.0.1:3420`. It needs no token. Use a local app or command line. The API rejects browser requests that carry an Origin header.

## Check the service

```powershell
curl.exe http://127.0.0.1:3420/health
```

A reply of `{"status":"ok","version":"0.2"}` means the server is up.

## Turn audio into text

```powershell
curl.exe -F "file=@clip.wav" "http://127.0.0.1:3420/transcribe?lang=mt"
curl.exe -H "Content-Type: audio/wav" --data-binary "@clip.wav" http://127.0.0.1:3420/transcribe
```

Leave out `lang` to use Auto. Use `mt`, `en`, or `it` to choose a pack. The reply has `text` and `language`. Maltese text goes through the fixer. Audio stays on this computer. The file limit is 25 MB.

## Fix Maltese spelling

```powershell
curl.exe -H "Content-Type: application/json" --data-binary "@fix.json" http://127.0.0.1:3420/fix
```

Save this in `fix.json` as UTF-8.

```json
{"text":"Nixtieq nghidlek xi haga"}
```

The reply is `{"text":"Nixtieq ngħidlek xi ħaġa"}`.

## Turn text into audio

```powershell
curl.exe -H "Content-Type: application/json" --data-binary "@speak.json" http://127.0.0.1:3420/speak --output spoken.mp3
```

Save this in `speak.json` as UTF-8.

```json
{"text":"Bonġu, kif inti?","lang":"mt"}
```

The reply is an MP3. Leave out `lang` to detect the language from the text. This route sends the text to an online voice service. Turn off online voices in settings to disable it. The text limit is 12,000 characters.

A failed request returns a JSON object with an `error` field.
