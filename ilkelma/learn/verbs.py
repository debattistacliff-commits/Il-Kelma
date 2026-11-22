"""Il-Kelma's own Maltese verb maker.

Regular verbs are built from a few stems with the rules of Maltese grammar.
Irregular verbs are written out by hand. Every form is then checked against the
MLRS word data (Korpus Malti); forms not found there are marked for a person to check."""
from contextlib import closing
import re
import sqlite3

PERSONS = ["jiena", "inti", "huwa", "hija", "aħna", "intom", "huma"]

# perfect: (1st sg, 3rd sg m, 3rd sg f, 3rd pl), imperfect: (1st sg, 1st pl), imperative: (sg, pl)
REGULAR = {
    "kiteb": (("ktibt", "kiteb", "kitbet", "kitbu"), ("nikteb", "niktbu"), ("ikteb", "iktbu"), ("write", "writes", "wrote")),
    "fehem": (("fhimt", "fehem", "fehmet", "fehmu"), ("nifhem", "nifhmu"), ("ifhem", "ifhmu"), ("understand", "understands", "understood")),
    "ħareġ": (("ħriġt", "ħareġ", "ħarġet", "ħarġu"), ("noħroġ", "noħorġu"), ("oħroġ", "oħorġu"), ("go out", "goes out", "went out")),
    "daħal": (("dħalt", "daħal", "daħlet", "daħlu"), ("nidħol", "nidħlu"), ("idħol", "idħlu"), ("come in", "comes in", "came in")),
    "xorob": (("xrobt", "xorob", "xorbot", "xorbu"), ("nixrob", "nixorbu"), ("ixrob", "ixorbu"), ("drink", "drinks", "drank")),
    "ħadem": (("ħdimt", "ħadem", "ħadmet", "ħadmu"), ("naħdem", "naħdmu"), ("aħdem", "aħdmu"), ("work", "works", "worked")),
    "ħasel": (("ħsilt", "ħasel", "ħaslet", "ħaslu"), ("naħsel", "naħslu"), ("aħsel", "aħslu"), ("wash", "washes", "washed")),
    "fetaħ": (("ftaħt", "fetaħ", "fetħet", "fetħu"), ("niftaħ", "niftħu"), ("iftaħ", "iftħu"), ("open", "opens", "opened")),
}

# Irregular verbs, written by hand: imperfect, perfect, imperative (yes, no) per person.
IRREGULAR = {
    "mar": {
        "english": ("go", "goes", "went"),
        "imperfett": (["immur", "tmur", "imur", "tmur", "immorru", "tmorru", "imorru"],
                      ["ma mmurx", "ma tmurx", "ma jmurx", "ma tmurx", "ma mmorrux", "ma tmorrux", "ma jmorrux"]),
        "perfett": (["mort", "mort", "mar", "marret", "morna", "mortu", "marru"],
                    ["ma mortx", "ma mortx", "ma marx", "ma marritx", "ma morniex", "ma mortux", "ma marrux"]),
        "imperattiv": (["mur", "morru"], ["tmurx", "tmorrux"]),
    },
    "ta": {
        "english": ("give", "gives", "gave"),
        "imperfett": (["nagħti", "tagħti", "jagħti", "tagħti", "nagħtu", "tagħtu", "jagħtu"],
                      ["ma nagħtix", "ma tagħtix", "ma jagħtix", "ma tagħtix", "ma nagħtux", "ma tagħtux", "ma jagħtux"]),
        "perfett": (["tajt", "tajt", "ta", "tat", "tajna", "tajtu", "taw"],
                    ["ma tajtx", "ma tajtx", "ma tax", "ma tatx", "ma tajniex", "ma tajtux", "ma tawx"]),
        "imperattiv": (["agħti", "agħtu"], ["tagħtix", "tagħtux"]),
    },
}


# Loan verbs ending in -a (bbukkja = to book) all follow one pattern.
LOAN = {
    "bbukkja": ("book", "books", "booked"), "bbenefika": ("benefit", "benefits", "benefited"),
    "bbaża": ("base", "bases", "based"), "bbilanċja": ("balance", "balances", "balanced"),
    "bbrilla": ("shine", "shines", "shone"), "bblokka": ("block", "blocks", "blocked"),
    "bbalzma": ("embalm", "embalms", "embalmed"), "bbumbardja": ("bomb", "bombs", "bombed"),
    "bbrejkja": ("brake", "brakes", "braked"), "bbojkottja": ("boycott", "boycotts", "boycotted"),
    "bbaġitja": ("budget", "budgets", "budgeted"), "bbasta": ("be enough", "is enough", "was enough"),
    "bbottilja": ("bottle", "bottles", "bottled"),
}


def loan(lemma):
    """bbukkja: nibbukkja ... bbukkjajt ... ibbukkja."""
    return regular((lemma + "jt", lemma, lemma + "t", lemma + "w"),
                   ("ni" + lemma, "ni" + lemma + "w"), ("i" + lemma, "i" + lemma + "w"))


def negative(form):
    """ma + form + x. Before -x the last 'e' becomes 'i' (kiteb -> ma kitibx); -na becomes -niex."""
    if form.endswith("na"):
        return "ma " + form[:-2] + "niex"
    if form.endswith("u"):
        return "ma " + form + "x"
    return "ma " + re.sub(r"e(?=[^aeiou]*$)", "i", form) + "x"


def regular(perfect, imperfect, imperative):
    first, third_m, third_f, third_pl = perfect
    stem = first[:-1]  # ktibt -> ktib
    perfect_yes = [first, first, third_m, third_f, stem + "na", stem + "tu", third_pl]
    singular, plural = imperfect[0][1:], imperfect[1][1:]  # nikteb -> ikteb
    imperfect_yes = ["n" + singular, "t" + singular, "j" + singular, "t" + singular,
                     "n" + plural, "t" + plural, "j" + plural]
    imperative_no = [negative("t" + singular)[3:], negative("t" + plural)[3:]]
    return {"imperfett": (imperfect_yes, [negative(f) for f in imperfect_yes]),
            "perfett": (perfect_yes, [negative(f) for f in perfect_yes]),
            "imperattiv": (list(imperative), imperative_no)}


def tables(forms):
    titles = {"imperfett": "Imperfett · present", "perfett": "Perfett · past", "imperattiv": "Imperattiv · command"}
    result = []
    for tense in ("imperfett", "perfett", "imperattiv"):
        yes, no = forms[tense]
        persons = ["inti", "intom"] if tense == "imperattiv" else PERSONS
        result.append({"title": titles[tense], "head": ["persuna", "pożittiv", "negattiv"],
                       "rows": [list(row) for row in zip(persons, yes, no)]})
    return result


def all_verbs():
    """{lemma: (tables, english forms, how it was made)}"""
    verbs = {}
    for lemma, (perfect, imperfect, imperative, english) in REGULAR.items():
        verbs[lemma] = tables(regular(perfect, imperfect, imperative)), english, "rules"
    for lemma, english in LOAN.items():
        verbs[lemma] = tables(loan(lemma)), english, "rules"
    for lemma, data in IRREGULAR.items():
        verbs[lemma] = tables(data), data["english"], "by hand"
    return verbs


def unknown_forms(verb_tables, db_path):
    """Forms (without 'ma') that are not in the MLRS word data."""
    words = {word for table in verb_tables for row in table["rows"] for cell in row[1:]
             for word in cell.split() if word != "ma"}
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as db:
        return sorted(word for word in words
                      if db.execute("SELECT 1 FROM words WHERE word=?", (word,)).fetchone() is None)
