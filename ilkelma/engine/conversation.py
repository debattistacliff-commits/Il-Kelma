"""Talk with Buddy in Maltese.

Everything runs on the user's own computer through the local AI helper."""
import json
import re
from urllib.request import Request, urlopen

from .maltese_fix import fix
from .phrasing import LOCAL_AI_URL

TALK_MODEL = "gemma3:12b"

TALK_RULES = (
    "You are Kelma, a warm, patient friend who helps a Maltese person practise written and spoken "
    "Maltese. Il-Kelma is made for people with dyslexia, so use short, simple, correct standard Maltese with the letters "
    "ċ ġ ħ ż għ. Reply as JSON only: {\"correct\": ..., \"reply\": ...}. "
    "\"correct\" is what the person said, rewritten in correct Maltese spelling and grammar, keeping "
    "their meaning and words (the same text if it was already right). "
    "\"reply\" is your friendly answer in one or two short Maltese sentences, often ending with a "
    "simple question so the talk continues."
)

def ask(messages, *, model=TALK_MODEL, timeout=240, json_reply=False):
    body = {"model": model, "stream": False, "keep_alive": "10m", "messages": messages,
            "options": {"temperature": 0.6, "num_ctx": 4096}}
    if json_reply:
        body["format"] = "json"
    request = Request(LOCAL_AI_URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)["message"]["content"].strip()


def talk(said, history=(), *, model=TALK_MODEL):
    """(said in correct Maltese, Buddy's reply). history is [(said, reply), ...]."""
    messages = [{"role": "system", "content": TALK_RULES}]
    for before, answer in list(history)[-6:]:
        messages += [{"role": "user", "content": before},
                     {"role": "assistant", "content": json.dumps({"correct": before, "reply": answer},
                                                                 ensure_ascii=False)}]
    messages.append({"role": "user", "content": said})
    content = ask(messages, model=model, json_reply=True)
    try:
        data = json.loads(content)
        if not isinstance(data, dict) or not data.get("reply"):
            raise ValueError("unexpected reply")
        correct, reply = str(data.get("correct") or said), str(data["reply"])
    except (ValueError, KeyError, TypeError):
        correct, reply = said, content
    return fix(clean(correct), lang="mt"), fix(clean(reply), lang="mt")


def clean(text, keep_lines=False):
    text = re.sub(r"[*#_`]+", "", text).strip().strip('"“”')
    if keep_lines:
        return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())
    return " ".join(text.split())


def same(said, correct):
    words = lambda text: re.findall(r"[^\W\d_]+", text.casefold())
    return words(said) == words(correct)
