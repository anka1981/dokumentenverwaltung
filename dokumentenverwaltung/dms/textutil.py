"""Tokenisierung und Normalisierung für deutschsprachige Dokumente."""

import re
import unicodedata

# Häufige deutsche (und einige englische) Wörter, die nichts über den Inhalt
# eines Dokuments aussagen. Die Liste ist bereits normalisiert (siehe norm()).
_STOPWORDS_RAW = """
aber alle allem allen aller alles als also am an ander andere anderem anderen
anderer anderes anders auch auf aus bei beim bin bis bist bitte da dabei dadurch
dafuer dagegen daher dahin damals damit danach daneben dann daran darauf daraus
darf darin darueber darum darunter das dass dasselbe dein deine deinem deinen
deiner dem demnach den denen denn dennoch der deren derer des deshalb dessen
dich die dies diese diesem diesen dieser dieses dir doch dort du durch eben ebenso
ein eine einem einen einer eines einig einige einmal er es etwa etwas euch euer
eure fuer ganz gar gegen gemaess gewesen gibt ging habe haben hat hatte hatten
hier hin hinter ich ihm ihn ihnen ihr ihre ihrem ihren ihrer ihres im immer in
indem ins ist ja jede jedem jeden jeder jedes jedoch jene jetzt kann kein keine
keinem keinen keiner koennen koennte machen man manche mehr mein meine meinem
meinen meiner mich mir mit muss musste nach nachdem neben nein nicht nichts noch
nun nur ob oder ohne per pro schon sehr sein seine seinem seinen seiner seit
selbst sich sie sind so sodass solche soll sollen sollte sondern sowie ueber um
und uns unser unsere unserem unseren unter viel vom von vor waehrend war waren
warum was weil weiter welche welchem welchen welcher welches wenn wer werde
werden wie wieder will wir wird wo wollen worden wurde wurden zu zum zur zwar
zwischen bzw ca ggf inkl evtl usw zzgl sowie hiermit bereits erhalten erfolgt
moechten moegen duerfen kannst koennen liegt lassen neue neuen neuer neues
geehrte geehrter geehrtes damen herren herr frau freundlichen gruessen gruss
mfg viele vielen dank danke seite seiten datum telefon tel fax email mail www
http https com de net org str strasse nr nummer betreff anlage anlagen ort
kontakt hinweis hinweise information informationen fragen frage gerne jederzeit
heute morgen gestern bitten beiliegend anbei folgende folgenden folgt sofern
falls insgesamt gesamt januar februar maerz april mai juni juli august
september oktober november dezember montag dienstag mittwoch donnerstag freitag
samstag sonntag euro eur the and for with you your this that from are was
unten oben siehe ab zb ihren ihrem ihres unserer unseres dieses jahr jahres
monat monate monats tag tage tagen woche wochen gmbh ag kg ohg ev mbh co
"""

STOPWORDS = frozenset(_STOPWORDS_RAW.split())

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def norm(text: str) -> str:
    """Kleinschreibung, Umlaute ausgeschrieben – robust gegen OCR-Varianten."""
    text = unicodedata.normalize("NFKC", text).lower().translate(_UMLAUTS)
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def tokens(text: str) -> list[str]:
    """Alle normalisierten Wörter eines Textes (inkl. Stoppwörter)."""
    return _TOKEN_RE.findall(norm(text))


def content_tokens(text: str) -> list[str]:
    """Normalisierte Wörter, die inhaltlich etwas aussagen."""
    return [t for t in tokens(text) if is_content_word(t)]


def is_content_word(tok: str) -> bool:
    if len(tok) < 3 or tok in STOPWORDS:
        return False
    if any(c.isdigit() for c in tok):
        return False
    return True


def surface_words(text: str):
    """Liefert (normalisiert, Originalschreibung) für jedes Wort."""
    for m in _TOKEN_RE.finditer(unicodedata.normalize("NFC", text)):
        word = m.group(0)
        n = norm(word)
        if n:
            yield n, word


def safe_filename(name: str, max_len: int = 120) -> str:
    """Macht aus einem beliebigen Titel einen gültigen Dateinamen."""
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:max_len].rstrip(" .") or "Dokument"
