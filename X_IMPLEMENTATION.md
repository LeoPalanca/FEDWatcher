# FedWatcher — X (Twitter) Sentiment Implementation

## Role in the System

X/Twitter sentiment is an **experimental third input** to the StrategistAgent — not a new agent. It acts as a real-time proxy for market participant interpretation of Fed communications, complementing the primary LLM analysis of official documents.

The core pipeline (Fed docs → LLM → nowcast) works without it. Twitter is a toggle.

---

## Economic Rationale

Fed officials and market participants actively use X to interpret and react to FOMC communications. Three measurable signals exist:

1. **Immediate reaction tone**: in the 30–60 minutes after an FOMC release, tweets from economists, rates strategists, and Fed watchers reveal whether sophisticated market participants read the statement as hawkish or dovish — often before prices fully adjust
2. **Between-meeting drift**: Fed officials (and their advisors) sometimes signal evolving views via speeches that get amplified on X before formal documents are released
3. **Disagreement signal**: high variance in tweet sentiment around a release = ambiguous communication = higher uncertainty = wider bid/ask in rates

Academic grounding: López-Lira & Tang (2023) show Twitter/X sentiment predicts next-day equity returns. The mechanism is similar here but applied to rates.

---

## Data Source

**X API v2** — Basic tier (free) provides:
- 500,000 tweets/month read access
- Search by keyword + time range
- Up to 7 days historical

**Search queries:**

```python
QUERIES = {
    "fomc_reaction": '("FOMC" OR "Fed" OR "Powell") (hawkish OR dovish OR "rate hike" OR "rate cut") lang:en -is:retweet',
    "fomc_minutes":  '("FOMC minutes" OR "Fed minutes") lang:en -is:retweet',
    "fed_speech":    '("Fed speech" OR "Powell speech" OR "Waller" OR "Williams Fed") lang:en -is:retweet',
}

# Filter to high-signal accounts only
HIGH_SIGNAL_ACCOUNTS = [
    # Fed watchers, rates strategists, economists with verified track records
    # Keep list in config — easy to update
]
```

Rate limit: 15 requests / 15 min on free tier. Sufficient for event-driven polling (trigger on new Fed document, not continuous).

---

## Implementation

### `sources/twitter.py`

```python
import tweepy
from agents.analyst import AnalystAgent

class TwitterSource:
    def __init__(self, bearer_token: str, analyst: AnalystAgent):
        self.client = tweepy.Client(bearer_token=bearer_token)
        self.analyst = analyst

    def fetch_reaction_tweets(self, trigger_time: str, window_minutes: int = 60) -> list[dict]:
        """
        Fetch tweets in `window_minutes` after a Fed document release.
        trigger_time: ISO timestamp of document release
        """
        start = pd.Timestamp(trigger_time)
        end   = start + pd.Timedelta(minutes=window_minutes)

        tweets = self.client.search_recent_tweets(
            query      = QUERIES["fomc_reaction"],
            start_time = start.isoformat(),
            end_time   = end.isoformat(),
            max_results = 100,
            tweet_fields = ["created_at", "author_id", "public_metrics"],
        )
        return [{"text": t.text, "created_at": t.created_at,
                 "likes": t.public_metrics["like_count"],
                 "retweets": t.public_metrics["retweet_count"]}
                for t in (tweets.data or [])]

    def get_sentiment(self, trigger_time: str) -> dict | None:
        """
        Returns aggregated sentiment dict in same shape as AnalystAgent output,
        or None if insufficient tweets fetched.
        """
        tweets = self.fetch_reaction_tweets(trigger_time)
        if len(tweets) < 10:          # not enough signal
            return None

        # Weight by engagement
        weighted_text = self._build_weighted_corpus(tweets)

        # Reuse same LLM prompt as AnalystAgent — same output schema
        return self.analyst.score_text(
            text     = weighted_text,
            doc_type = "twitter_reaction",
        )

    def _build_weighted_corpus(self, tweets: list[dict]) -> str:
        """
        Sort by engagement, take top 20, concatenate.
        Engagement = likes + 2*retweets (retweets carry more signal).
        """
        scored = sorted(
            tweets,
            key=lambda t: t["likes"] + 2 * t["retweets"],
            reverse=True
        )
        return "\n".join(f"- {t['text']}" for t in scored[:20])
```

---

## Integration into StrategistAgent

Twitter score becomes a weighted input to the divergence computation. The weight is configurable and defaults to zero (off):

```python
# agents/strategist.py

SENTIMENT_WEIGHTS = {
    "fed_docs":    0.80,
    "ecb_docs":    0.10,
    "twitter":     0.10,   # set to 0.0 to disable
}

def compute_blended_tone(self, fed_sentiment, ecb_sentiment=None, twitter_sentiment=None) -> float:
    scores  = {"fed_docs": fed_sentiment["tone_score"]}
    weights = {"fed_docs": SENTIMENT_WEIGHTS["fed_docs"]}

    if ecb_sentiment:
        scores["ecb_docs"]  = ecb_sentiment["tone_score"]
        weights["ecb_docs"] = SENTIMENT_WEIGHTS["ecb_docs"]

    if twitter_sentiment and SENTIMENT_WEIGHTS["twitter"] > 0:
        scores["twitter"]  = twitter_sentiment["tone_score"]
        weights["twitter"] = SENTIMENT_WEIGHTS["twitter"]

    total_w = sum(weights.values())
    return sum(scores[k] * weights[k] for k in scores) / total_w
```

---

## Database

Add one column to `sentiment` table — no new table needed:

```sql
ALTER TABLE sentiment ADD COLUMN twitter_tone_score FLOAT;
ALTER TABLE sentiment ADD COLUMN twitter_tweet_count INTEGER;
```

---

## Dashboard Panel (Optional Extension)

Add to existing Panel 2 (Sentiment Time Series):

- Dashed line overlay: Twitter reaction tone at each FOMC release
- Tooltip: "Twitter sentiment (n=42 tweets, top accounts)" 
- Color band: shaded region when Twitter and Fed doc tone diverge strongly

Useful for presentation: shows real-time market interpretation vs. systematic LLM reading.

---

## Known Limitations

| Issue | Impact | Mitigation |
|-------|--------|------------|
| X API free tier = 7 days lookback only | No historical Twitter backfill | Use as live-only layer; backtest uses Fed docs only |
| Bot/spam accounts | Noise in sentiment | Filter to accounts with >1000 followers + engagement threshold |
| LLM scoring tweet aggregates ≠ scoring official text | Different register, more informal | Flag in output as `source: twitter_reaction`, keep weight low (0.10) |
| Rate limits (15 req/15 min) | Can't poll continuously | Event-driven only: trigger on new Fed document, not on schedule |
| X API ToS changes | Could break at any time | Wrap in try/except; pipeline continues without Twitter signal if fetch fails |

---

## Rollout Plan

| Phase | Action |
|-------|--------|
| Core done | Add `TWITTER_BEARER_TOKEN` to `.env.example` |
| Experimental | Implement `TwitterSource`, wire into `pipeline.py` behind `USE_TWITTER=false` env flag |
| Validated | Tune weight from 0.10 upward if backtesting shows improvement |
| Presentation stretch | Show live tweet reaction panel on dashboard for one FOMC meeting |
