"""
News Intelligence Agent.

Clusters multi-source news articles into deduplicated, discrete NewsEvents.
Extracts affected entities, event dates, sentiment, and market/corporate impact.
Eliminates reporting redundancy by linking multiple sources to single underlying events.
"""
import re
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from datahunt.models.shared_intel import NewsEvent, FreshnessRating
from datahunt.agents.freshness import FreshnessAgent
from datahunt.logger import logger


_POSITIVE_WORDS = {
    "surge", "jump", "rally", "profit", "growth", "expansion", "beat", "strong",
    "upgrade", "record", "order", "contract", "breakthrough", "partnership", "success"
}
_NEGATIVE_WORDS = {
    "fall", "drop", "slump", "loss", "decline", "probe", "lawsuit", "penalty",
    "downgrade", "debt", "miss", "weak", "resignation", "investigation", "layoff", "cut"
}
_HIGH_IMPACT_TERMS = {
    "acquisition", "merger", "earnings", "quarterly results", "regulatory probe",
    "sebi", "sec", "bankruptcy", "default", "ipo", "delisting", "dividend"
}


class NewsIntelligenceAgent:
    """
    Transforms noisy, redundant news headlines and articles into deduplicated NewsEvents.
    """
    def __init__(self, freshness_agent: Optional[FreshnessAgent] = None):
        self.freshness_agent = freshness_agent or FreshnessAgent()

    def cluster_articles(self, articles: List[Dict[str, Any]]) -> List[NewsEvent]:
        """
        Clusters raw articles into discrete events based on entity mentions and textual overlap.
        """
        if not articles:
            return []

        clusters: List[Dict[str, Any]] = []

        for art in articles:
            title = str(art.get("title") or "").strip()
            summary = str(art.get("summary") or art.get("snippet") or "").strip()
            url = art.get("url") or art.get("link") or ""
            date_str = art.get("date") or art.get("published_at")
            entities = list(art.get("entities", []))

            # Tokenize title
            tokens = set(re.findall(r"\b[a-zA-Z]{4,}\b", title.lower()))

            matched_cluster = None
            for cl in clusters:
                overlap = len(tokens & cl["tokens"])
                if overlap >= 3 or (len(tokens) > 0 and overlap / len(tokens) >= 0.5):
                    matched_cluster = cl
                    break

            if matched_cluster:
                if url and url not in matched_cluster["sources"]:
                    matched_cluster["sources"].append(url)
                for ent in entities:
                    if ent not in matched_cluster["entities"]:
                        matched_cluster["entities"].append(ent)
                if len(summary) > len(matched_cluster["summary"]):
                    matched_cluster["summary"] = summary
            else:
                clusters.append({
                    "title": title or "Corporate / Market Development",
                    "summary": summary or title,
                    "tokens": tokens,
                    "entities": entities,
                    "date": date_str,
                    "sources": [url] if url else [],
                })

        events: List[NewsEvent] = []
        for cl in clusters:
            full_text = f"{cl['title']} {cl['summary']}".lower()

            # Sentiment calculation
            pos_score = sum(1 for w in _POSITIVE_WORDS if w in full_text)
            neg_score = sum(1 for w in _NEGATIVE_WORDS if w in full_text)

            if pos_score > neg_score:
                sentiment = "positive"
            elif neg_score > pos_score:
                sentiment = "negative"
            else:
                sentiment = "neutral"

            # Impact calculation
            has_high_impact = any(t in full_text for t in _HIGH_IMPACT_TERMS)
            impact = "high" if has_high_impact else ("medium" if len(cl["sources"]) >= 2 else "low")

            # Freshness assessment
            fresh_eval = self.freshness_agent.evaluate_freshness(
                item_id=cl["title"],
                field="news_date",
                published_at=cl.get("date"),
                domain="market_news",
            )

            event = NewsEvent(
                title=cl["title"],
                summary=cl["summary"],
                entities=cl["entities"],
                date=cl.get("date"),
                sources=cl["sources"],
                sentiment=sentiment,
                impact=impact,
                freshness=fresh_eval.rating,
                confidence=min(1.0, 0.70 + 0.10 * len(cl["sources"])),
            )
            events.append(event)

        logger.info(f"NewsIntelligenceAgent: Clustered {len(articles)} articles into {len(events)} events")
        return events
