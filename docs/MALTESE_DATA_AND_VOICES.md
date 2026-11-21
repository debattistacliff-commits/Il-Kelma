# Maltese data and voices

## Word list

- Korpus Malti v4 comes from the University of Malta's Maltese Language Resource Server (MLRS). Licence is CC BY-NC-SA 4.0. The corpus counts are for non-commercial use. Credit MLRS and keep the same licence when sharing those counts. [Corpus paper by Kurt Micallef and colleagues](https://aclanthology.org/2022.deeplo-1.10/)
- MUDT is the Maltese Universal Dependencies Treebank by Slavomír Čéplö. Licence is CC BY-SA 4.0. The builder reads the sentence text from its three splits.
- Clifford's Maltese language bank supplies approved or verified forms. The builder keeps them apart from the word counts.

Use this command to build or resume the word list.

```powershell
.venv\Scripts\python.exe -B build_wordlist.py
```

The builder saves counts and the source file position together. Run the same command after a stop. If a source file changes, build a fresh copy in a clean project folder. Each row in the `progress` table shows how far the builder read.

The builder uses every available genre. The publisher also supplies a shuffled copy of the same sentences. The builder counts the genre files once. [Maltese Language Resource Server](https://mlrs.research.um.edu.mt/)

The word list keeps forms such as `tal-Malti` together, in lower case. It counts pairs with a space between the words. Add names and places to `data/my_words.txt`, one per line. The spelling helper keeps these words as typed.

The spelling helper checks each sentence for Maltese cues. It can add letters to a word on its own when one spelling has at least 50 hits and is at least 20 times as common as the next spelling. Pass `lang="en"` or `lang="it"` to keep the full text as typed.

## Read aloud

`.venv\Scripts\python.exe -B speak.py "Bonġu, kif inti?" --lang mt`

Leave out `--lang` to guess from the text. Use `--voice mt-MT-JosephNeural` to try Joseph on his own. Use `--save-only` to save without opening the player.

The online neural voices use an online voice service to turn text into speech. It needs internet access. Only use it with text you are willing to send. The script saves an MP3 in `out/` and opens it in the Windows audio player.
