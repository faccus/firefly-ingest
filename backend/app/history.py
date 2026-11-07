"""Suggerimenti di categoria dalle transazioni simili già presenti in Firefly.

Niente AI: per ogni riga senza categoria si cercano nello storico le transazioni
con controparte uguale oppure con descrizione simile (sovrapposizione di parole),
e si propone la categoria più frequente se è abbastanza concorde.
"""

import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

STOPWORDS = {
    "pagamento", "pos", "carta", "presso", "del", "della", "dei", "con", "per", "the", "srl", "spa", "snc", "sas",
    "bonifico", "addebito", "accredito", "operazione", "data", "ore", "eur", "euro", "italia", "italy", "via",
    "n", "nr", "num", "card", "mastercard", "visa", "sdd", "sepa", "cod", "disp", "favore", "vostro",
}
TOKEN_RE = re.compile(r"[a-zà-ù]{3,}")
MIN_SCORE = 0.5
MIN_AGREEMENT = 0.6


def tokens(*texts: str | None) -> frozenset[str]:
    out: set[str] = set()
    for t in texts:
        if t:
            out.update(w for w in TOKEN_RE.findall(t.lower()) if w not in STOPWORDS)
    return frozenset(out)


def norm_party(name: str | None) -> str | None:
    if not name or name.startswith("("):  # "(no name)", "(cash)"
        return None
    n = re.sub(r"[^a-z0-9]", "", name.lower())
    return n or None


@dataclass(frozen=True)
class HistTx:
    category: str
    party: str | None
    toks: frozenset[str]
    budget: str | None = None


class HistoryIndex:
    def __init__(self, txs: list[HistTx]):
        self.txs = txs
        self.by_party: dict[str, Counter[str]] = defaultdict(Counter)
        self.by_token: dict[str, list[int]] = defaultdict(list)
        for i, t in enumerate(txs):
            if t.party:
                self.by_party[t.party][t.category] += 1
            for w in t.toks:
                self.by_token[w].append(i)

    def suggest(self, description: str, counterparty: str | None) -> tuple[str, str] | None:
        """(categoria, motivo) oppure None."""
        party = norm_party(counterparty)
        if party and party in self.by_party:
            cat, n = self.by_party[party].most_common(1)[0]
            total = sum(self.by_party[party].values())
            if n / total >= MIN_AGREEMENT:
                return cat, f"stessa controparte ({n}/{total})"
        toks = tokens(description, counterparty)
        if not toks:
            return None
        candidates = {i for w in toks for i in self.by_token.get(w, ())}
        votes: Counter[str] = Counter()
        matched = 0
        for i in candidates:
            t = self.txs[i]
            score = len(toks & t.toks) / len(toks | t.toks)
            if score >= MIN_SCORE:
                votes[t.category] += score
                matched += 1
        if not votes:
            return None
        cat, weight = votes.most_common(1)[0]
        if weight / sum(votes.values()) < MIN_AGREEMENT:
            return None
        return cat, f"{matched} transazioni simili"


def from_firefly_groups(groups: list[dict[str, Any]]) -> list[HistTx]:
    out: list[HistTx] = []
    for g in groups:
        for t in g["attributes"]["transactions"]:
            cat = t.get("category_name")
            if not cat or t.get("type") == "transfer":
                continue
            party = t.get("destination_name") if t.get("type") == "withdrawal" else t.get("source_name")
            out.append(
                HistTx(
                    category=cat,
                    party=norm_party(party),
                    toks=tokens(t.get("description"), party if party and not party.startswith("(") else None),
                    budget=t.get("budget_name"),
                )
            )
    return out


_cache: tuple[float, HistoryIndex] | None = None
CACHE_SECONDS = 600


def invalidate() -> None:
    global _cache
    _cache = None


async def get_index(ff: Any, months: int = 18) -> HistoryIndex:
    global _cache
    if _cache and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    groups = await ff.all_transactions(months)
    idx = HistoryIndex(from_firefly_groups(groups))
    _cache = (time.monotonic(), idx)
    return idx
