# FedWatcher README — Formula & Code Review

## Overall Assessment

Ambitious, well-structured project. Shows understanding of rates desk workflow. But several formula errors, finance literature gaps, and architectural concerns need fixing before implementation.

---

## FORMULA & MODEL FLAWS (Critical)

### 1. Nowcasting Model — Multinomial, Not Binary (Section 6.1)

```
P(hike | tone_score, Δcpi, unemployment_gap) = σ(β₀ + β₁·tone + β₂·Δcpi + β₃·u_gap)
```

**Problem:** Binary logistic regression models only P(hike). But FOMC decisions are **three outcomes**: hike, hold, cut. You then write:

```
implied_rate = current_rate + P(hike)·0.25 - P(cut)·0.25
```

But P(cut) was never estimated. This formula is internally inconsistent. Binary logistic gives you P(hike) and P(not hike) = 1 - P(hike). Where does P(cut) come from?

**Fix:** Use **multinomial logistic regression** (or ordered logit, which is more natural here since hike > hold > cut). Then you get P(hike), P(hold), P(cut) that sum to 1. Or estimate two separate binaries: P(hike vs. not) and P(cut vs. not), but that's messier.

### 2. Implied Rate Formula — Missing Non-Standard Moves

```
implied_rate = current_rate + P(hike)·0.25 - P(cut)·0.25
```

**Problem:** Assumes all moves are exactly 25bps. During 2022-2023, Fed hiked 50bps and 75bps multiple times. During COVID (March 2020), cut 150bps total in two emergency meetings. Your training data (2007-2024) includes these.

**Fix:** Either model move *size* separately, or use a richer outcome space: {-50, -25, 0, +25, +50, +75} bps. CME FedWatch itself uses this granularity.

### 3. EWMA Smoothing — Irregular Time Series Problem (Section 5.3)

```
tone_t = λ·score_t + (1-λ)·tone_{t-1},   λ = 0.3
```

**Problem:** EWMA assumes equally-spaced observations. FOMC meetings are ~6 weeks apart, but speeches happen randomly. A burst of 3 speeches in one week would dominate tone far more than intended. Minutes released 3 weeks after statement would get equal weight despite being about same meeting.

**Fix:** Time-weighted EWMA where λ depends on elapsed time:
```
λ_t = 1 - exp(-Δt / halflife)
```
Or weight by document type × time gap. Also: should speech from random Fed Governor count same as Chair press conference? Probably not.

### 4. Section Weight Aggregation — Static Weights Are Fragile

Weights (0.40, 0.30, 0.20, 0.10) are hardcoded. No justification given. "Committee Policy Action" getting 0.40 makes sense, but:
- These weights should be validated empirically (do they maximize predictive power for next rate decision?)
- Different document types (statements vs minutes vs speeches) have different sections — how do weights apply to a speech with no "Committee Policy Action" section?

---

## FINANCE LITERATURE ISSUES

### 5. Mischaracterization of Market Efficiency (Section 2)

> "Residual drift in rates over 1–5 days following releases suggests that full parsing of lengthy documents takes time — creating a window for systematic extraction (Swanson, 2021)"

**Problem:** Swanson (2021) is about measuring effects of forward guidance and QE, NOT about slow market incorporation of FOMC text. You're citing it for a claim it doesn't make. Post-announcement drift in rates is contested and thin in high-frequency Treasury data. This is not equity momentum — rate markets are dominated by sophisticated players with existing NLP tools.

**Fix:** If you want to argue markets are slow to parse, cite **Bauer & Swanson (2023)** "A Reassessment of Monetary Policy Surprises and High-Frequency Identification" or **Cieslak & Schrimpf (2019)** on information processing in rate markets. Or soften claim: say you're measuring divergences, not claiming markets are wrong.

### 6. Taylor Rule Claim Needs Nuance

> "Hawk/dove scores derived from textual analysis outperform simple Taylor rule forecasts at horizons of 1–3 meetings (Apel & Grimaldi, 2012; Lucca & Trebbi, 2009)"

**Problem:** Lucca & Trebbi (2009) doesn't compare against Taylor rule — it measures tone of FOMC statements. Apel & Grimaldi is about Riksbank, not Fed. Be precise about which central bank each paper studies.

### 7. Missing Key Literature

You cite no work on:
- **Hansen & McMahon (2016)** — "Shocking Language" — seminal paper on decomposing Fed communication into topics using LDA. Direct predecessor to your approach
- **Shapiro & Wilson (2022)** — Fed NLP at the Fed itself
- **Bholat et al. (2015)** — Bank of England text mining
- **Cieslak, Morse & Vissing-Jorgensen (2019)** — "Stock Returns over the FOMC Cycle" — important context for why tone matters for asset prices

### 8. Loughran-McDonald Misuse

> "Loughran-McDonald, 2011. These methods fail... 'inflation is not above target' is scored hawkishly"

**Problem:** Loughran-McDonald is for 10-K filings, not central bank text. It doesn't have hawk/dove categories at all — it has positive/negative/uncertainty/litigious. You're conflating two different things: general financial dictionaries vs. purpose-built Fed dictionaries (like those in Lucca & Trebbi or Hansen & McMahon).

---

## ARCHITECTURAL / DATA ISSUES

### 9. Fed Funds Futures Formula

> "The implied Fed Funds rate is extracted as `100 - futures_price`"

**Problem:** This gives you rate for specific contract month, not meeting-by-meeting probability. Converting FF futures to meeting-by-meeting probabilities requires accounting for meeting dates within contract months. CME's own methodology uses:

```
P(hike) = (implied_rate - current_rate) / 0.25
```

But adjusted for where in month meeting falls. You need day-weighted averaging. See CME FedWatch methodology paper.

### 10. OIS Data Source — Availability Problem

> "OIS forward rates sourced from CME FedWatch Tool API (free access)"

**Problem:** CME FedWatch Tool doesn't have a public API. It's a web tool. You can scrape it, but that's fragile and possibly against ToS. OIS rates from Bloomberg/Reuters require paid terminal. FRED has SOFR and Fed Funds rate, but not OIS forward curves.

**Fix:** Be honest about data source limitations. For academic project, Fed Funds futures from `yfinance` + SOFR from FRED is sufficient. Don't claim OIS access you don't have.

### 11. Confidence Field — LLM Self-Assessment Is Unreliable

> "confidence field is required and scores below 0.6 are flagged for manual review"

**Problem:** LLMs are notoriously poorly calibrated when self-reporting confidence. GPT/Claude confidence scores don't correlate well with actual accuracy. This is documented extensively (Kadavath et al., 2022).

**Fix:** Better approach: use inter-model agreement as confidence proxy (you mention this as optional — make it primary). Or use embedding-space distance from training examples.

### 12. 10bps Divergence Threshold — Not Justified

> "Significance threshold: |divergence| > 10 basis points triggers a signal alert"

**Problem:** No justification. 10bps in 2-year rates is meaningful; 10bps in overnight rate expectation is also meaningful but for different reasons. Is this 10bps robust across rate regimes? In 2015 when rates were 0.25%, 10bps = 40% of rate. In 2023 when rates were 5.25%, 10bps = 2% of rate.

**Fix:** Use a z-score relative to historical divergence distribution, not absolute level. Or calibrate to bid-ask spread of FF futures.

---

## MINOR ISSUES

13. **Training sample bias**: 2007-2024 includes ZLB (2009-2015) where forward guidance was the *only* tool. Model learned on this period may overweight language vs. macro data. Acknowledge this explicitly.

14. **Beige Book weight**: Listed as "Medium" signal strength but Beige Book is qualitative economic survey, not policy signal. Academic literature (Balke & Petersen, 2002) shows limited incremental info.

15. **`pdfplumber` for Fed docs**: Fed statements are HTML, not PDF. Minutes are also HTML on website. PDF parsing only needed for speeches. Small point but shows implementation hasn't been tested.

16. **No backtesting framework described**: You describe training nowcasting model but never mention backtesting the full pipeline (LLM sentiment → divergence → signal accuracy). This is critical for academic credibility.

17. **Schema**: `ff_futures_implied FLOAT[]` — PostgreSQL arrays work but are awkward to query. Better as separate table with meeting dates as rows.

---

## Summary — Top 5 Things To Fix

| Priority | Issue | Impact |
|----------|-------|--------|
| 1 | Binary logit → multinomial/ordered logit | Formula broken as written |
| 2 | Implied rate formula ignores non-25bps moves | Wrong during 2022-23 training period |
| 3 | Swanson (2021) miscitation + add Hansen & McMahon | Academic credibility |
| 4 | OIS data source doesn't exist as described | Can't implement |
| 5 | Add backtesting framework | Prof will ask about this |
