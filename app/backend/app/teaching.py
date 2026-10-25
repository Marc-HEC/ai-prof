"""Outils du prof : tableau blanc et recherche web.

Le LLM n'appelle pas de fonctions : KoboldCpp gère mal les appels d'outils
natifs, et un petit modèle les rate souvent. Il écrit plutôt des blocs dans sa
réponse, comme il écrit déjà les balises `[emotion:joie]` :

    <tableau>
    flowchart LR
      A[Évaporation] --> B[Condensation]
    </tableau>

    <recherche>dernier prix Nobel de physique</recherche>

`BlockParser` retire ces blocs du flux avant le découpage en phrases, donc ils
ne sont jamais lus à voix haute. Un bloc `tableau` est envoyé au navigateur. Un
bloc `recherche` arrête la réponse en cours : la session cherche, puis relance
le LLM avec les sources pour qu'il termine sa réponse.
"""
from __future__ import annotations

import asyncio
import re

BLOCK_NAMES = ("tableau", "recherche")
_OPEN_RE = re.compile(r"<\s*(tableau|recherche)\s*>", re.IGNORECASE)
MAX_BLOCK_CHARS = 4000  # un bloc jamais fermé ne doit pas avaler toute la réponse
MERMAID_STARTS = ("flowchart", "graph", "sequencediagram", "mindmap", "timeline", "pie", "classdiagram")

RESEARCH_FOLLOWUP = (
    "Résultats de ta recherche « {query} ». Termine ta réponse à l'oral en t'appuyant dessus, "
    "cite la source en quelques mots, et ne refais pas de recherche.\n\n{sources}"
)
RESEARCH_UNAVAILABLE = (
    "La recherche « {query} » n'a rien donné ({reason}). Dis-le simplement et réponds avec ce que tu sais."
)


class BlockParser:
    """Sépare le texte parlé des blocs `<tableau>` et `<recherche>`, au fil des jetons."""

    def __init__(self) -> None:
        self.buf = ""
        self.block: str | None = None

    def push(self, token: str) -> list[tuple[str, str]]:
        self.buf += token
        out: list[tuple[str, str]] = []
        while True:
            if self.block is None:
                m = _OPEN_RE.search(self.buf)
                if m:
                    if m.start():
                        out.append(("text", self.buf[: m.start()]))
                    self.block = m.group(1).lower()
                    self.buf = self.buf[m.end():]
                    continue
                # Garde la fin du tampon si elle peut être le début d'une balise.
                cut = self.buf.rfind("<")
                if cut != -1 and _could_open(self.buf[cut:]):
                    if cut:
                        out.append(("text", self.buf[:cut]))
                    self.buf = self.buf[cut:]
                elif self.buf:
                    out.append(("text", self.buf))
                    self.buf = ""
                return out
            close = re.search(rf"<\s*/\s*{self.block}\s*>", self.buf, re.IGNORECASE)
            if close:
                out.append((self.block, self.buf[: close.start()].strip()))
                self.buf = self.buf[close.end():]
                self.block = None
                continue
            if len(self.buf) > MAX_BLOCK_CHARS:
                out.append((self.block, self.buf.strip()))
                self.buf, self.block = "", None
            return out

    def flush(self) -> list[tuple[str, str]]:
        """Fin de réponse : un bloc non fermé est quand même rendu, le reste est du texte."""
        rest, block = self.buf, self.block
        self.buf, self.block = "", None
        if block:
            return [(block, rest.strip())] if rest.strip() else []
        return [("text", rest)] if rest and not _could_open(rest) else []


def _could_open(tail: str) -> bool:
    head = re.sub(r"\s+", "", tail[1:].lower())
    return any(name.startswith(head) for name in BLOCK_NAMES) and ">" not in tail


def board_event(content: str) -> dict | None:
    """Traduit un bloc `tableau` en message pour le navigateur."""
    content = content.strip()
    if not content:
        return None
    first = content.split("\n", 1)[0].strip().lower().replace(" ", "")
    if first.startswith(MERMAID_STARTS):
        return {"type": "board", "op": "schema", "mermaid": content}
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return {"type": "board", "op": "notes", "lines": lines[:20]}


def format_sources(sources: list[dict]) -> str:
    return "\n\n".join(
        f"[{i}] {s.get('titre', '')} ({s.get('url', '')})\n{s.get('extrait', '')}" for i, s in enumerate(sources, 1)
    )


def tools_prompt(tools: list[str], web_search: bool) -> str:
    """Consignes ajoutées au prompt système quand la persona a des outils."""
    parts: list[str] = []
    if "tableau" in tools:
        parts.append(
            "## Ton tableau blanc\n"
            "Tu as un tableau blanc que l'élève voit. Sers-t'en dès qu'un schéma, une formule, "
            "un calcul étape par étape ou un plan aide à comprendre. Annonce-le d'abord en une phrase, "
            "puis écris un bloc qui ne sera pas lu à voix haute :\n"
            "- des lignes de texte ou de formules, une idée par ligne :\n"
            "  <tableau>\nThéorème de Pythagore\na² + b² = c²\n</tableau>\n"
            "- ou un schéma Mermaid simple, pour un enchaînement d'étapes :\n"
            "  <tableau>\nflowchart LR\n  A[Évaporation] --> B[Condensation] --> C[Précipitations]\n</tableau>\n"
            "Pour effacer le tableau, écris [tableau:effacer]. Ne lis jamais le contenu du bloc à voix haute, "
            "explique-le plutôt avec tes mots."
        )
    if "recherche_web" in tools and web_search:
        parts.append(
            "## Internet\n"
            "Pour une actualité, un chiffre récent ou une info dont tu n'es pas sûr, dis d'abord "
            "« je regarde ça » puis écris <recherche>ta question</recherche> et arrête-toi là. "
            "Tu recevras les résultats et tu pourras finir ta réponse. Une seule recherche par réponse. "
            "Ne mets jamais le nom ni une information personnelle de l'élève dans une recherche."
        )
    return "\n\n".join(parts)


# --- recherche web -------------------------------------------------------------
# C'est la seule sortie vers un tiers de toute l'app : la question part chez
# DuckDuckGo, puis ton PC télécharge les premières pages trouvées. D'où
# `recherche_web.enabled`, désactivé par défaut dans config.yaml.

def _ddgs_search(query: str, region: str, max_results: int) -> list[dict]:
    from ddgs import DDGS

    return list(DDGS().text(query, region=region, max_results=max_results))


def _extract(html: str) -> str | None:
    try:
        import trafilatura
    except ImportError:
        return None
    return trafilatura.extract(html)


async def web_search(query: str, cfg, client) -> list[dict]:
    """Retourne au plus `cfg.pages` sources {titre, url, extrait}."""
    results = await asyncio.to_thread(_ddgs_search, query, cfg.region, cfg.max_results)
    top = results[: cfg.pages]
    pages = await asyncio.gather(
        *[client.get(r["href"], timeout=cfg.timeout_s, follow_redirects=True) for r in top],
        return_exceptions=True,
    )
    sources = []
    for result, page in zip(top, pages):
        text = None
        if not isinstance(page, BaseException) and page.status_code == 200:
            text = await asyncio.to_thread(_extract, page.text)
        sources.append({
            "titre": result.get("title", ""),
            "url": result.get("href", ""),
            "extrait": (text or result.get("body", ""))[: cfg.excerpt_chars],
        })
    return sources
