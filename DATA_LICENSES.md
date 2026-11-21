# Data licences and credits

Il-Kelma's program code is MIT (see `LICENSE`). The language data comes from these sources, each with its own licence.

| Data | Used for | Source | Licence |
|---|---|---|---|
| `maltese_words.sqlite` (release `data-v1`) | Maltese spelling fixer: word forms and word pairs | **Korpus Malti v4**, MLRS, University of Malta; **MUDT** treebank; Clifford's Maltese language bank | **CC BY-NC-SA 4.0** (Korpus Malti) · CC BY-SA 4.0 (MUDT). Free, non-commercial, share-alike. |
| `macocu_words.sqlite` (release `data-v1`) | Extra Maltese word counts | **MaCoCu-mt 2.0**, Bañón et al. 2023, [CLARIN.SI 11356/1803](https://hdl.handle.net/11356/1803) | CC0 1.0 |
| `sentences.sqlite` (release `data-v1`) | "Help me say it" sentence bank | **MaCoCu-mt-en 2.0**, Bañón et al. 2023, [CLARIN.SI 11356/1811](https://hdl.handle.net/11356/1811) | CC0 1.0 |
| `data/spell_mt_pairs.json`, `sources/spell-mt/` | Spelling test pairs | **SPELL-MT** | CC BY 4.0 (see `sources/spell-mt/LICENSE`) |
| `sources/hunspell-mt/` | Maltese spelling dictionary | **hunspell-mt** | LGPL 2.1 (see `sources/hunspell-mt/licence.txt`) |
| `data/maltese_basics.json` | Starter phrases | Clifford's Maltese language bank | Shared by Clifford for this project |
| `assets/fonts/` | Pad letters | Lexend, Atkinson Hyperlegible, Andika, Comic Neue, Plus Jakarta Sans | SIL Open Font License (files included) |

Speech recognition models are downloaded by `download_models.py` and keep their own licences. Read-aloud uses online neural voices, and the computer's own voices offline.

**Because of Korpus Malti's licence, Il-Kelma's Maltese data is free and may not be sold.**

Thanks to the Maltese Language Resource Server (MLRS) at the University of Malta, the MaCoCu project, the SPELL-MT and hunspell-mt authors, and the Handy project (cjpais, MIT), which inspired the idea (no code copied).
