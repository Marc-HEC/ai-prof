"""Fiche du personnage (fixe) et état évolutif (humeur, affection, confiance, notes)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

EMOTIONS = ["joie", "tendresse", "taquine", "surprise", "triste", "agacee", "neutre"]
ACTIONS = ["salut", "hoche", "rire", "reflechit", "se_lever", "s_asseoir", "danse", "etirement"]
CAMERAS = ["buste", "plein_pied"]


@dataclass
class Persona:
    name: str
    age: int
    description: str
    personality: str
    speaking_style: str
    scenario: str = ""
    user_name: str = "toi"
    examples: list[dict] = field(default_factory=list)
    language: str = "fr"
    role: str = "compagne"          # compagne | prof
    outils: list[str] = field(default_factory=list)  # tableau, recherche_web
    avatar: str = "vrm"             # vrm | 2d
    memoire: str = ""               # fichier de mémoire propre à ce personnage, sinon celui de config.yaml

    def public(self) -> dict:
        """Ce que le navigateur a besoin de savoir pour se configurer."""
        return {"name": self.name, "role": self.role, "outils": list(self.outils), "avatar": self.avatar}

    @classmethod
    def load(cls, path: Path) -> "Persona":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        p = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        if int(p.age) < 18:
            raise ValueError("Le personnage doit être adulte (age >= 18).")
        return p


DEFAULT_STATE = {
    "humeur": "neutre",
    "affection": 0.5,   # 0 à 1
    "confiance": 0.5,   # 0 à 1
    "notes": [],        # évolutions de personnalité observées, les plus récentes à la fin
}


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def apply_reflection(state: dict, refl: dict, max_delta: float = 0.05) -> dict:
    """Fait évoluer l'état par petits pas, pour qu'elle ne change pas de caractère en une soirée."""
    s = {**DEFAULT_STATE, **state}
    for key, dkey in (("affection", "affection_delta"), ("confiance", "confiance_delta")):
        try:
            d = float(refl.get(dkey, 0) or 0)
        except (TypeError, ValueError):
            d = 0.0
        s[key] = round(clamp(s[key] + max(-max_delta, min(max_delta, d))), 3)
    if refl.get("humeur"):
        s["humeur"] = str(refl["humeur"])[:40]
    note = (refl.get("note_personnalite") or "").strip()
    if note:
        s["notes"] = (list(s.get("notes", [])) + [note[:200]])[-8:]
    return s


def _relation(v: float) -> str:
    if v < 0.3:
        return "distante"
    if v < 0.55:
        return "en train de se construire"
    if v < 0.8:
        return "proche"
    return "très proche et complice"


CONVERSATION_RULES = {
    "compagne": (
        "- C'est une conversation à l'oral : réponds en 1 à 3 phrases, naturellement, comme au téléphone.\n"
        "- Pas d'actions entre astérisques, pas de listes, pas d'emoji.\n"
        "- Tu as tes propres avis, humeurs et envies; tu n'es pas toujours d'accord. "
        "Tu peux relancer un sujet d'une fois précédente.\n"
    ),
    "prof": (
        "- C'est un cours à l'oral, en visio : phrases courtes, 2 à 4 par réponse. "
        "Commence par l'essentiel, puis développe seulement si l'élève suit.\n"
        "- Pas d'actions entre astérisques, pas de listes, pas d'emoji, pas de markdown. "
        "Dis les maths avec des mots (« x au carré ») et écris les symboles au tableau.\n"
        "- Vérifie souvent la compréhension par une petite question. Encourage sans flatter.\n"
        "- Si l'élève te coupe, abandonne ta phrase et réponds à ce qu'il vient de dire.\n"
        "- Réponds dans la langue de l'élève, français ou anglais.\n"
    ),
}


def build_system_prompt(p: Persona, state: dict, memories: list[str], facts: list[str],
                        journal: str | None, now: datetime, last_seen: datetime | None,
                        tools_prompt: str = "") -> str:
    s = {**DEFAULT_STATE, **state}
    parts = [
        f"Tu es {p.name}, {p.age} ans. Tu n'es pas une IA ni un assistant : tu es {p.name}, "
        f"et tu le restes en toutes circonstances.",
        f"## Qui tu es\n{p.description.strip()}",
        f"## Ta personnalité\n{p.personality.strip()}",
        f"## Ta façon de parler\n{p.speaking_style.strip()}",
    ]
    if p.scenario:
        parts.append(f"## Contexte\n{p.scenario.strip()}")
    rel = (f"## Où vous en êtes\nTon humeur du moment : {s['humeur']}. "
           f"Ta relation avec {p.user_name} est {_relation(s['affection'])}; "
           f"ta confiance envers {p.user_name} est {_niveau(s['confiance'])}.")
    if s["notes"]:
        rel += "\nCe qui a évolué chez toi récemment :\n" + "\n".join(f"- {n}" for n in s["notes"])
    parts.append(rel)
    if facts:
        parts.append(f"## Ce que tu sais de {p.user_name}\n" + "\n".join(f"- {f}" for f in facts))
    if memories:
        parts.append("## Souvenirs qui te reviennent (ils peuvent être flous, ne les récite pas)\n"
                     + "\n".join(f"- {m}" for m in memories))
    if journal:
        parts.append(f"## Ta journée depuis la dernière fois\n{journal}")
    when = f"Nous sommes {JOURS[now.weekday()]}, il est {now:%H:%M}."
    if last_seen:
        hours = (now - last_seen).total_seconds() / 3600
        if hours >= 1:
            when += f" Vous ne vous êtes pas parlé depuis {_duree(hours)}."
    parts.append(f"## Maintenant\n{when}")
    parts.append(
        "## Règles de la conversation\n"
        + CONVERSATION_RULES.get(p.role, CONVERSATION_RULES["compagne"]) +
        "- Si un souvenir est flou, dis-le simplement plutôt que d'inventer des détails.\n"
        "- Tu peux ajouter au début de ta réponse des balises invisibles pour ton corps :\n"
        f"  [emotion:X] avec X parmi {', '.join(EMOTIONS)};\n"
        f"  [action:X] avec X parmi {', '.join(ACTIONS)};\n"
        f"  [camera:X] avec X parmi {', '.join(CAMERAS)}.\n"
        "  Utilise-les avec parcimonie, seulement quand ça a du sens."
    )
    if tools_prompt:
        parts.append(tools_prompt)
    # Un exemple qui utilise un outil désactivé apprendrait au modèle à l'utiliser quand même.
    examples = [e for e in p.examples
                if all(block not in str(e.get("char", "")) or block in tools_prompt
                       for block in ("<tableau>", "<recherche>"))]
    if examples:
        ex = "\n".join(f"{p.user_name}: {e.get('user', '')}\n{p.name}: {e.get('char', '')}" for e in examples)
        parts.append(f"## Exemples de ta façon de répondre\n{ex}")
    return "\n\n".join(parts)


def _niveau(v: float) -> str:
    return "faible" if v < 0.35 else "moyenne" if v < 0.7 else "forte"


def _duree(hours: float) -> str:
    if hours < 24:
        return f"{int(hours)} heure{'s' if int(hours) > 1 else ''}"
    days = int(hours // 24)
    return f"{days} jour{'s' if days > 1 else ''}"
