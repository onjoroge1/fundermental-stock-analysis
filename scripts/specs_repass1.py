"""Re-pass batch (2026-08-07): the 17 reports invalidated by new filings
during the July-24→Aug-7 refresh freeze. Merge order lets these override all
earlier specs. Where a prior thesis failed, the post-mortem is stated in the
report — updating beats defending."""

from specs_extra import S, C, sc

REPASS = {}

REPASS["AAPL"] = S(
    "Post-FY26-Q3 re-pass: the quarter was excellent — revenue $109.4B, TTM "
    "revenue +16.4% YoY with operating income +26.6% — but the market paid "
    "for it twice: 35.7x earnings at the 90th percentile of own history, "
    "with the reverse DCF now requiring 21.1%/yr against a +1.8% delivered "
    "3-year CAGR (older flat years still dominate the CAGR).",
    "IMPROVING", "STRONG",
    ["TTM revenue +16.4%, OI +26.6% — the re-acceleration is real and filed",
     "Beat streak: +6.7%, +3.1%, +6.9%"],
    ["P/E 35.7 at the 90th percentile; FCF yield just 3.0%",
     "Insiders sold $86.7M, zero buys"],
    ["Whether the AI-device upgrade cycle sustains mid-teens growth",
     "Services mix vs hardware cyclicality"],
    sc((8.0, 26.0), (9.3, 31.0), (10.3, 36.0), 0.30, 0.50, 0.20),
    "Great business, re-rated faster than it re-accelerated: the expectations "
    "gap (+25pt implied-vs-achieved) is now among the widest in mega-cap "
    "tech. The quarter validated the business; the price pre-paid several "
    "more like it.",
    ["If mid-teens growth is the new base rate, the 3-yr CAGR anchor understates trend"],
    ["Sustained ~21%/yr growth at a $4.6T scale"],
    ["Earnings 2026-10-29", "Holiday-quarter device cycle data"],
    ["Multiple compression to even 28x is a -22% move with no business miss",
     "China demand and tariff exposure persist"],
    ["Revenue growth below 8%", "Services growth below 10%", "Gross margin below 45%"],
    "The +37% twelve-month run priced the re-acceleration before proving its "
    "durability; at 35.7x with a 3.0% FCF yield, AAPL is again a "
    "sentiment asset. Insider selling into the rally concurs.",
    ["Base case holds a 31x multiple — historically rich for this name",
     "3-yr CAGR anchor may genuinely understate the new trend (stated both ways)"],
    ["None — earnings quality remains clean"],
    ["90th-percentile P/E on a re-accelerating but $467B-revenue base"],
    ["Device-cycle unit data (not in filings)"],
    "WATCH", "MEDIUM", 50,
    [C("FY2026-Q3 revenue was $109.4B; TTM revenue grew 16.4% YoY with operating income +26.6%.",
       "FACT", "SEC:ACCESSION:0000320193-26-000020"),
     C("The shares trade at 35.7x TTM earnings (90th percentile) with a 3.0% FCF yield.",
       "FACT", "YAHOO:CHART:AAPL"),
     C("Price-implied growth (21.1%/yr) exceeds delivered 3-year CAGR (+1.8%) by 25 points.",
       "FACT", "YAHOO:CHART:AAPL", "SEC:ACCESSION:0000320193-26-000020"),
     C("Probability-weighted 12-month value is roughly at the current price.",
       "FORECAST")])

REPASS["MSFT"] = S(
    "Post-FY26-Q4 re-pass with a changed verdict: the +31% rally since the "
    "July report consumed the thesis. Revenue +17.8% and a 46.8% operating "
    "margin remain elite, but TTM FCF margin collapsed to 20.2% ($67.0B) "
    "under the AI capex program, and the price now requires 34.4%/yr — "
    "double the delivered 16.1%. Downgrading ATTRACTIVE → WATCH: the "
    "expectations gap that made the case has closed.",
    "IMPROVING", "STRONG",
    ["Revenue +17.8% YoY, OM 46.8%, beats +6.2%/+5.2%/+11.8%",
     "Azure/AI demand remains supply-constrained per results"],
    ["FCF margin 20.2% — capex is eating half of operating cash generation",
     "Implied growth (34.4%/yr) now double delivered (16.1%)"],
    ["AI capex → revenue conversion lag", "FCF trough timing"],
    sc((16.0, 24.0), (19.0, 28.0), (21.5, 33.0), 0.25, 0.50, 0.25),
    "The July thesis (excellent business, reasonable expectations) was "
    "correct and is now spent: +31% of multiple expansion moved MSFT from "
    "the cheapest expectations in mega-cap software to among the richest. "
    "Position note: the paper long captured the move; the re-pass takes "
    "the win off the table.",
    ["If Azure sustains 30%+ with capex peaking, FCF snaps back and re-rates the base"],
    ["~34%/yr growth — a rate MSFT has never delivered at this scale"],
    ["Earnings 2026-10-28", "Capex guidance inflection"],
    ["AI capex without matching revenue conversion compresses both FCF and multiple",
     "1.8% FCF yield offers no valuation support"],
    ["Azure growth below 25%", "FCF margin below 18%", "Capex above 40% of OCF for two more quarters"],
    "Everything good about this quarter was pre-paid by the rally: at a "
    "1.8% FCF yield, MSFT is priced beyond its own delivered growth with "
    "cash conversion at a decade low. The July buyers' edge is gone.",
    ["Bear multiple 24x is itself generous if FCF stays depressed"],
    ["None — the FCF compression is disclosed capex, not quality"],
    ["Implied-vs-delivered gap of +30pt after the re-rating"],
    ["Azure-specific disclosure (segment parsing pending)"],
    "WATCH", "MEDIUM", 52,
    [C("FY2026-Q4 revenue was $90.0B (+17.8% TTM YoY) with a 46.8% operating margin.",
       "FACT", "SEC:ACCESSION:0001193125-26-323660"),
     C("TTM FCF fell to $67.0B (20.2% margin) under the AI capex program; FCF yield is 1.8%.",
       "FACT", "SEC:ACCESSION:0001193125-26-323660", "YAHOO:CHART:MSFT"),
     C("The +31% rally since 2026-07-25 closed the expectations gap that grounded the prior ATTRACTIVE call.",
       "INFERENCE", "YAHOO:CHART:MSFT"),
     C("Probability-weighted 12-month value is ~+8% above the current price — no longer compensation for the risk.",
       "FORECAST")])

REPASS["AMZN"] = S(
    "Post-FY26-Q2 re-pass with TWO active invalidation breaches: receivables "
    "grew 33.8pt faster than revenue (rule thresholds 20/25 both fired) and "
    "TTM FCF deepened to NEGATIVE $11.6B. Revenue +19.6% and OI +43.2% are "
    "strong; TTM net income ($135.3B) now exceeds operating income ($93.7B) "
    "— large non-operating items inflate GAAP EPS and the 22.1x P/E is not "
    "what it looks like. Downgrading WATCH → UNATTRACTIVE until the "
    "working-capital and cash pictures resolve.",
    "MIXED", "MODERATE",
    ["Revenue +19.6% YoY; operating income +43.2% — the margin story continues",
     "Beats continue (+70.6%, +215.9% — magnitudes distorted by low bases)"],
    ["Receivables gap widened to +33.8pt — the breach the July report named as its top risk",
     "TTM FCF -$11.6B; NI > OI by $41.6B (non-operating items, auto-flagged)"],
    ["Whether receivables convert to cash or charge-offs", "Capex cycle exit timing"],
    sc((10.0, 18.0), (12.0, 22.0), (14.0, 26.0), 0.35, 0.45, 0.20),
    "The July report said 'watch the receivables' — they got worse while "
    "the stock rose 18%. A $3.0T company with negative trailing FCF, a "
    "widening vendor-financing signature, and mark-inflated GAAP earnings "
    "is priced on trust the working capital contradicts.",
    ["If the receivables build is benign BNPL scaling, the operating story resumes intact"],
    ["Clean margin expansion with no working-capital cost"],
    ["Earnings 2026-10-29", "Receivables disclosure in the 10-Q notes"],
    ["Breach escalation: gap >40pt would rhyme with DELL's vendor-financing pattern",
     "Consumer credit exposure through a downturn"],
    ["Receivables gap above 40pt", "FCF below -$15B", "OI growth under 15%"],
    "Both flagged risks materialized and the market paid a higher price "
    "anyway: EPS-based multiples are inflated by non-operating gains, cash "
    "flow is negative, and receivables are compounding faster than revenue. "
    "This is the weakest quality picture in mega-cap.",
    ["Scenario EPS uses OPERATING-basis proxy (~) because GAAP EPS is mark-inflated",
     "Base case assumes the receivables resolve benignly"],
    ["NI exceeds OI by >25% (auto-flag); receivables breach active"],
    ["Headline 22x P/E overstates cheapness — operating basis is materially higher"],
    ["Receivables composition (10-Q note parsing pending)"],
    "UNATTRACTIVE", "MEDIUM", 38,
    [C("FY2026-Q2 revenue was $200.6B (+19.6% TTM YoY) with operating income +43.2%.",
       "FACT", "SEC:ACCESSION:0001018724-26-000026"),
     C("Receivables grew 33.8 points faster than revenue — both invalidation rules fired; TTM FCF is -$11.6B.",
       "FACT", "SEC:ACCESSION:0001018724-26-000026", "SM:INVALIDATION:AMZN"),
     C("TTM net income ($135.3B) exceeds operating income ($93.7B) — GAAP EPS and P/E are inflated by non-operating items.",
       "FACT", "SEC:ACCESSION:0001018724-26-000026"),
     C("Probability-weighted 12-month value is ~15% below the current price.",
       "FORECAST")])

REPASS["UBER"] = S(
    "Post-FY26-Q2 re-pass, thesis intact and partially paid: +13.6% since "
    "the July report. Operating income +30.3% on +12.2% revenue, FCF $10.1B, "
    "and the price STILL implies just 2.3%/yr against 17.7% delivered — the "
    "expectations gap narrowed but remains the widest negative in large-cap. "
    "ATTRACTIVE stands with trimmed E[r].",
    "IMPROVING", "STRONG",
    ["OI +30.3% YoY; FCF $10.1B at a 6.6% yield; P/E 16.0",
     "One director bought; expectations score 92"],
    ["Revenue growth decelerated to +12.2% (from +14.5%)",
     "CEO's $479M sale remains the standing insider counterweight"],
    ["AV partnership economics", "Bookings deceleration slope"],
    sc(50.6, 84.0, 123.5, 0.25, 0.50, 0.25),
    "Still priced for stagnation while compounding: the quarter beat, cash "
    "conversion held, and the implied-growth requirement stayed near zero. "
    "The remaining question is deceleration slope, not viability.",
    ["AV-supply aggregation continues to read as opportunity, not obituary"],
    ["Near-zero growth: +2.3%/yr implied"],
    ["Earnings 2026-11-03", "AV partnership volume disclosures"],
    ["Deceleration below 10% would validate the market's slow-growth pricing",
     "Equity-stake marks keep GAAP EPS noisy"],
    ["Revenue growth below 8% for two quarters", "FCF margin below 12%"],
    "The deceleration (14.5% → 12.2%) is the bear's first real data point: "
    "if growth converges to GDP-plus faster than the margin story "
    "compounds, today's 16x earnings is fair, not cheap.",
    ["Base case holds 15x P/FCF through the AV narrative overhang"],
    ["NI > OI persists (equity marks) — FCF remains the honest lens"],
    ["None at a 6.6% FCF yield"],
    ["Mobility/delivery segment split (pending)"],
    "ATTRACTIVE", "MEDIUM", 64,
    [C("FY2026-Q2 revenue was $14.2B; TTM operating income grew 30.3% with FCF of $10.1B (6.6% yield).",
       "FACT", "SEC:ACCESSION:0001543151-26-000032"),
     C("Price-implied growth remains +2.3%/yr vs +17.7% delivered.",
       "FACT", "YAHOO:CHART:UBER"),
     C("Revenue growth decelerated from +14.5% to +12.2% YoY — the key trend to monitor.",
       "FACT", "SEC:ACCESSION:0001543151-26-000032"),
     C("Probability-weighted 12-month value is ~+14% above the current price.",
       "FORECAST")])

REPASS["AMD"] = S(
    "Post-FY26-Q2 re-pass: revenue +50.1% YoY with operating income up "
    "15.9x off last year's base — the datacenter ramp is delivering — and "
    "the stock still trades at 124x earnings requiring 50.0%/yr for five "
    "years, ABOVE consensus's +23.5%. The paper short is up (price fell "
    "$522→$481 since entry); UNATTRACTIVE stands on arithmetic, not on "
    "doubt about the business.",
    "IMPROVING", "STRONG",
    ["Revenue +50.1% YoY; MI-series datacenter share gains are real",
     "Beats: +15.9%, +6.2%, +2.5%"],
    ["ROIC still 8.3% under acquisition goodwill",
     "Implied 50%/yr vs consensus +23.5% next FY"],
    ["Hyperscaler custom-silicon substitution", "2027 AI-capex digestion risk"],
    sc((7.0, 35.0), (10.5, 40.0), (14.0, 48.0), 0.30, 0.45, 0.25),
    "Everything the bulls promised is happening and the price needs more "
    "than that: at 124x GAAP earnings, even 50% growth delivered TWICE "
    "leaves the multiple doing the heavy lifting. Same verdict, better "
    "entry than July.",
    ["If inference demand compounds through 2028, the requirement could be met for 2-3 years"],
    ["Five years of ~50% growth — above consensus"],
    ["Earnings 2026-11-03", "MI-series roadmap wins"],
    ["Digestion year compresses E and multiple together",
     "Custom silicon takes the marginal hyperscaler dollar"],
    ["Datacenter growth below 30%", "Gross margin below 48%"],
    "A 15.9x OI multiple-off-base year is the EASY comp; the 124x multiple "
    "prices the hard ones. When 50% growth meets the law of large numbers, "
    "there is no valuation floor above $200.",
    ["Bull case needs 48x on $14 EPS — perfection squared"],
    ["Goodwill-suppressed ROIC (8.3%) still lags the narrative"],
    ["124x GAAP / 18.8x EV-revenue for a cyclical"],
    ["Client vs datacenter mix (pending)"],
    "UNATTRACTIVE", "LOW", 37,
    [C("FY2026-Q2 revenue was $11.5B (+50.1% TTM YoY) with operating income up ~16x off the prior-year base.",
       "FACT", "SEC:ACCESSION:0000002488-26-000123"),
     C("The shares trade at 124x TTM earnings; price-implied growth (50.0%/yr) exceeds consensus (+23.5%).",
       "FACT", "YAHOO:CHART:AMD", "FMP:ESTIMATES:AMD"),
     C("The business is delivering; the valuation still requires more than delivery.",
       "INFERENCE", "SEC:ACCESSION:0000002488-26-000123"),
     C("Probability-weighted 12-month value is ~20% below the current price.",
       "FORECAST")])

REPASS["PLTR"] = S(
    "Post-FY26-Q2 re-pass and an honest post-mortem: the July UNATTRACTIVE "
    "call is DOWN 40% as a short — revenue accelerated to +92.8% YoY with a "
    "42.8% operating margin and the market re-rated to 70x revenue. The "
    "machine's framework cannot justify the price (60.7%/yr implied) and "
    "could not have caught the move; the classification softens to WATCH "
    "because shorting hypergrowth on valuation alone is a demonstrated "
    "losing rule, not because the price became defensible.",
    "IMPROVING", "STRONG",
    ["Revenue +92.8% YoY — acceleration at scale; OM 42.8%",
     "Beats +8.6%, +13.8%, +19.0%; FCF margin 54.6%"],
    ["EV/revenue 70.1 — the highest large-cap multiple on record in this universe",
     "Insider selling $196.4M continues"],
    ["US gov + commercial AI contract velocity", "Law-of-large-numbers arrival"],
    sc((1.2, 70.0), (1.8, 95.0), (2.6, 125.0), 0.30, 0.45, 0.25),
    "Post-mortem first: July's short thesis was arithmetically right and "
    "directionally wrong — hypergrowth + narrative beats valuation "
    "discipline on any 3-month window. The honest lesson is process: "
    "valuation-only shorts on accelerating names are banned from the paper "
    "book going forward. The stock remains priced for ubiquity (60.7%/yr).",
    ["If PLTR becomes the enterprise AI OS, decade-scale growth could meet the requirement"],
    ["Ubiquity: five years above 60%/yr"],
    ["Earnings 2026-11-02", "Commercial segment run-rate"],
    ["Any deceleration below 60% likely halves the multiple",
     "Insider distribution at scale persists"],
    ["Revenue growth below 50%", "Commercial growth below government growth two quarters running"],
    "Nothing about 70x revenue is safer after a +40% rally; the change is "
    "in our process, not the price. The name stays untouchable in both "
    "directions: unshortable on momentum, unbuyable on arithmetic.",
    ["Scenario multiples are conjecture at this altitude — stated plainly"],
    ["SBC 13.6% of revenue"],
    ["70x revenue; 146x earnings; 8th percentile P/E vs own history is meaningless at this scale shift"],
    ["None material"],
    "WATCH", "LOW", 40,
    [C("FY2026-Q2 revenue grew 92.8% YoY with a 42.8% operating margin and 54.6% FCF margin.",
       "FACT", "SEC:ACCESSION:0001321655-26-000041"),
     C("The July short thesis lost ~40%: the stock rose from $122.92 to $171.67 on acceleration.",
       "FACT", "YAHOO:CHART:PLTR"),
     C("Valuation-only shorts on accelerating hypergrowth are a demonstrated losing rule — process updated.",
       "INFERENCE", "YAHOO:CHART:PLTR"),
     C("The price still requires 60.7%/yr for five years; probability-weighted value remains below price.",
       "FORECAST")])

REPASS["SBUX"] = S(
    "Post-FY26-Q3 re-pass: the turnaround is showing operating proof — the "
    "quarter's OI implies ~10.5% margin and beats ran +17.6%/+28.8% — but "
    "revenue still FELL 1.4% and the shares trade at 61.4x with a 23.5%/yr "
    "implied requirement. Downgrade pressure eased; UNATTRACTIVE stands "
    "with a smaller expected shortfall.",
    "MIXED", "MODERATE",
    ["Quarterly operating margin ~10.5% — first real margin repair evidence",
     "Two consecutive large beats after the miss streak"],
    ["Revenue -1.4% YoY — traffic has not turned",
     "61.4x trailing earnings, 90th percentile"],
    ["Traffic inflection vs margin-only repair", "China stabilization"],
    sc((1.6, 32.0), (2.3, 36.0), (3.0, 42.0), 0.30, 0.50, 0.20),
    "The margin half of the turnaround arrived; the demand half did not. "
    "Paying 61x for cost repair without traffic is still pre-paying the "
    "full recovery — the gap simply shrank from absurd to expensive.",
    ["If traffic turns while margins rebuild, EPS compounds off both levers at once"],
    ["Complete recovery: 23.5%/yr implied"],
    ["Earnings 2026-10-28", "Quarterly traffic mix"],
    ["Margin repair without traffic is self-limiting (fewer hours, fewer visits)"],
    ["Another revenue decline next quarter", "Margin regression below 8%"],
    "Beats against collapsed estimates plus cost cuts is the classic "
    "false-dawn pattern; until customers return, the 61x multiple rests on "
    "hope with better expense control.",
    ["Base case assumes traffic flat, margins to 12% — both unproven"],
    ["None material"],
    ["90th-percentile P/E for negative revenue growth"],
    ["Traffic vs ticket decomposition (pending)"],
    "UNATTRACTIVE", "MEDIUM", 36,
    [C("FY2026-Q3 operating income was $0.98B on $9.3B revenue (~10.5% quarterly margin); TTM revenue fell 1.4%.",
       "FACT", "SEC:ACCESSION:0000829224-26-000130"),
     C("Recent surprises were +17.6% and +28.8% after a miss streak.",
       "FACT", "FMP:EARNINGS:SBUX"),
     C("Margin repair is real; demand repair is not yet in evidence.",
       "INFERENCE", "SEC:ACCESSION:0000829224-26-000130"),
     C("Probability-weighted 12-month value is ~25% below the current price.",
       "FORECAST")])

REPASS["SOFI"] = S(
    "Post-FY26-Q2 re-pass, thesis strengthening: revenue +42.5% with "
    "deposits +54.2% YoY, efficiency ratio improved to 82.0%, provisions "
    "still just 0.87% of revenue, and the CEO's four-buy cluster stands "
    "with zero sales. Up 10.4% since initiation. ATTRACTIVE stands; "
    "conviction remains LOW solely on the untested credit cycle.",
    "IMPROVING", "STRONG",
    ["Deposits +54.2% YoY to fund the flywheel; revenue +42.5%",
     "Beats: +12.8%, +0.8%, +9.1%; expectations score 99"],
    ["Efficiency ratio 82.0% — improving but far from mature-bank economics",
     "Fair-value loan book still cycle-untested"],
    ["Operating leverage slope", "Credit normalization timing"],
    sc((0.42, 26.0), (0.72, 31.0), (1.05, 42.0), 0.30, 0.45, 0.25),
    "The growth-bank thesis compounded exactly as drawn: faster deposits, "
    "better efficiency, benign credit, insider conviction intact. Price "
    "appreciation consumed some edge; the implied requirement (23.6%/yr) "
    "now sits at consensus rather than below delivered.",
    ["Bank-as-platform mix keeps revenue compounding above the priced rate"],
    ["~23.6%/yr — now roughly AT consensus, no longer below delivered"],
    ["Earnings 2026-10-27", "Deposit and efficiency prints"],
    ["First credit cycle reprices book and multiple together",
     "Dilution history could resume"],
    ["Deposit growth below 15%", "Provisions above 3% of revenue", "Revenue growth below 20%"],
    "Nothing in the quarter tested the only thing that matters long-run: "
    "loss content in a downturn. At 38.6x earnings, SOFI remains a "
    "credit-cycle bet wearing a growth multiple.",
    ["Scenario EPS assumes leverage through any normalization"],
    ["Fair-value accounting defers credit truth (standing flag)"],
    ["38.6x on 5%-ish ROE still requires the ROE-triples path"],
    ["Segment economics (pending)"],
    "ATTRACTIVE", "LOW", 60,
    [C("FY2026-Q2: revenue +42.5% TTM YoY, deposits $?B +54.2% YoY, efficiency ratio 82.0%, provisions 0.87% of revenue.",
       "FACT", "SEC:ACCESSION:0001818874-26-000054"),
     C("The CEO's four discretionary purchases stand with zero discretionary sales.",
       "FACT", "SEC:FORM4:SOFI"),
     C("Price-implied growth (23.6%/yr) has risen to meet consensus — some edge consumed by the +10% move.",
       "FACT", "YAHOO:CHART:SOFI"),
     C("Probability-weighted 12-month value is ~+22% above the current price, credit-cycle dependent.",
       "FORECAST")])

REPASS["ABNB"] = S(
    "Post-FY26-Q2 re-pass: operating momentum improved (revenue +16.5%, OI "
    "+23.9%) but the stock ran +23% since July and now asks 26.0%/yr — "
    "double the delivered 13.4% CAGR — at 38.5x earnings. The cash-flow "
    "tag gap PERSISTS in the new filing (FCF unverifiable, second "
    "consecutive quarter). WATCH with a negative tilt.",
    "IMPROVING", "MODERATE",
    ["Revenue +16.5% YoY with operating leverage (+23.9% OI)",
     "82.9% gross margin; net-cash balance sheet"],
    ["FCF fields missing AGAIN — cash story unverified two quarters running",
     "Implied 26%/yr vs 13.4% delivered; SBC 13.0% of revenue"],
    ["Experiences attach rate", "Regulatory city-level outcomes"],
    sc((4.2, 24.0), (5.2, 30.0), (6.2, 36.0), 0.30, 0.50, 0.20),
    "Better business, worse setup: the +23% run moved ABNB from "
    "fairly-priced to expectations-heavy while our data still cannot "
    "verify its cash conversion. Unverifiable FCF plus a doubled "
    "expectations bar caps this at WATCH.",
    ["Services expansion could genuinely raise the growth base"],
    ["Acceleration to ~26%/yr, double anything delivered"],
    ["Next earnings (calendar unavailable)", "ABNB cash-flow tag fix (queued)"],
    ["Travel cyclicality at premium multiples", "SBC persistently ~13%"],
    ["Revenue growth below 12%", "Nights growth decelerating two quarters"],
    "Paying 38.5x for a travel cyclical whose cash flow our pipeline "
    "cannot even see, priced for double its delivered growth, is momentum "
    "with an asterisk the market ignores and we cannot.",
    ["Scenario values rest on income-statement data alone (FCF unverified)"],
    ["Cash-flow statement fields absent two consecutive normalizations — data fix queued"],
    ["Implied-vs-delivered gap widened to +12.6pt"],
    ["ABNB cash-flow tag mapping (data fix pending)"],
    "WATCH", "LOW", 46,
    [C("FY2026-Q2 revenue was $3.61B (+16.5% TTM YoY) with operating income +23.9%.",
       "FACT", "SEC:ACCESSION:0001559720-26-000027"),
     C("Cash-flow fields are missing from a second consecutive normalization — FCF unverified.",
       "FACT", "SM:DATA_QUALITY:ABNB"),
     C("The +23% run doubled the expectations bar (26.0%/yr implied vs 13.4% delivered).",
       "FACT", "YAHOO:CHART:ABNB"),
     C("Probability-weighted 12-month value is ~7% below the current price.",
       "FORECAST")])

REPASS["BKNG"] = S(
    "Post-FY26-Q2 re-pass with a changed verdict: growth halved to +8.2% "
    "YoY exactly as the bear case warned, while the stock rallied +19% "
    "since July. The negative expectations gap that made this ATTRACTIVE "
    "has closed to -9.4pt on a decelerating base. Downgrading to WATCH.",
    "MIXED", "MODERATE",
    ["Operating margin held at 32.9%; FCF $9.5B (6.0% yield)",
     "P/E 22.7 at only the 15th percentile of own history"],
    ["Revenue growth halved: +16.2% → +8.2% YoY — the convergence-to-industry thesis is happening",
     "The July entry edge (+2%/yr implied) is now +4.3%/yr against slower delivery"],
    ["Whether deceleration stabilizes near 8% or continues toward GDP", "AI-agent booking disintermediation"],
    sc((8.5, 16.0), (10.0, 20.0), (11.5, 24.0), 0.30, 0.50, 0.20),
    "The thesis paid (+19%) and then the quarter validated the bear's "
    "mechanism: share-take growth is converging to industry growth. "
    "Taking the win; the remaining upside no longer compensates the "
    "deceleration risk.",
    ["Deceleration could be comp noise rather than trend — one more quarter decides"],
    ["Growth stabilization the latest print contradicts"],
    ["Next earnings", "Room-night growth trend"],
    ["Two more decelerating quarters put terminal-growth pricing in play"],
    ["Revenue growth below 6%", "Take-rate compression"],
    "Paying for a marketplace at 22.7x while its growth halves in two "
    "quarters is how convergence traps start; the cheap-vs-history framing "
    "dies when the history was a faster company.",
    ["Base case assumes growth floors at ~8%"],
    ["None material"],
    ["The expectations gap that justified entry is spent"],
    ["Agency/merchant mix (pending)"],
    "WATCH", "MEDIUM", 52,
    [C("FY2026-Q2 revenue grew 8.2% TTM YoY — half the prior +16.2% rate.",
       "FACT", "SEC:ACCESSION:0001075531-26-000037"),
     C("The stock returned +19% since the 2026-07-25 report; the implied-growth requirement rose to +4.3%/yr.",
       "FACT", "YAHOO:CHART:BKNG"),
     C("The paper long captured the expectations-gap closure; the remaining edge is spent.",
       "INFERENCE", "YAHOO:CHART:BKNG"),
     C("Probability-weighted 12-month value is ~4% below the current price.",
       "FORECAST")])

REPASS["CMG"] = S(
    "Post-FY26-Q2 re-pass: sequential improvement — revenue +9.3% with the "
    "OI decline moderating to -6.0% (from -17.2%) — but the oi_lt_0 "
    "invalidation remains formally breached. Upgrading UNATTRACTIVE → "
    "WATCH: the deterioration is decelerating and the multiple compressed "
    "to the 5th percentile of own history.",
    "MIXED", "MODERATE",
    ["OI decline moderated -17.2% → -6.0%; revenue +9.3%",
     "ROIC 62.8% — unit economics undamaged; P/E percentile now 5th"],
    ["Operating income still negative YoY (breach active)",
     "Traffic-led recovery unproven"],
    ["Two-quarter OI inflection", "Beef-cost cycle"],
    sc((1.05, 22.0), (1.30, 27.0), (1.55, 32.0), 0.30, 0.50, 0.20),
    "The July short logic (premium multiple, deteriorating momentum) "
    "played out — the stock sat flat while the market ran — and the "
    "de-rating has now done its work: 29.9x at the 5th percentile with "
    "improving comps is a balanced setup, not a short.",
    ["OI inflecting positive next quarter would restore the compounder narrative quickly"],
    ["Modest recovery (~15%/yr implied) — no longer aggressive"],
    ["Next earnings", "Comp traffic trend"],
    ["Margin pressure from beef/labor persists", "Fast-casual share war"],
    ["OI growth still negative in two more quarters", "Comp traffic negative"],
    "A 30x multiple for negative OI growth still isn't cheap — it's less "
    "expensive; the upgrade reflects closed downside, not opened upside.",
    ["Base case assumes the OI inflection completes within two quarters"],
    ["None material"],
    ["5th-percentile-vs-history multiple on still-declining income"],
    ["Traffic vs price mix (pending)"],
    "WATCH", "MEDIUM", 50,
    [C("FY2026-Q2 revenue grew 9.3% YoY while operating income declined 6.0% — moderating from -17.2%.",
       "FACT", "SEC:ACCESSION:0001058090-26-000066"),
     C("The oi_lt_0 invalidation rule remains breached; the P/E sits at the 5th percentile of five-year history.",
       "FACT", "SM:INVALIDATION:CMG", "YAHOO:CHART:CMG"),
     C("Deterioration is decelerating — the short case is spent, the long case unproven.",
       "INFERENCE", "SEC:ACCESSION:0001058090-26-000066"),
     C("Probability-weighted 12-month value approximates the current price.",
       "FORECAST")])

REPASS["DIS"] = S(
    "Post-FY26-Q3 re-pass, thesis strengthening: operating income +21.4% "
    "on +6.8% revenue with the streaming-margin story delivering, beats "
    "widening (+10.8% latest), the insider purchase standing, and the "
    "stock +10.6% since July. Upgrading WATCH → ATTRACTIVE at 21.3x with "
    "a still-modest bar.",
    "IMPROVING", "MODERATE",
    ["Operating income +21.4% YoY — streaming leverage is now the P&L's largest driver",
     "Beats: +3.8%, +5.4%, +10.8%; insider buy stands"],
    ["TTM EPS reset to $4.93 on content charges — P/E optics worsened while operations improved",
     "$41.7B net debt; linear decay continues inside the averages"],
    ["Streaming margin trajectory toward peers", "Parks resilience through the consumer cycle"],
    sc((4.6, 15.0), (5.6, 21.0), (6.4, 26.0), 0.25, 0.55, 0.20),
    "The transition is finally producing compounding operating income "
    "instead of promises: +21.4% OI growth twice-confirmed, at a multiple "
    "(21.3x) that prices moderate recovery rather than franchise "
    "restoration. The 30th-percentile valuation with improving delivery "
    "tips this to ATTRACTIVE.",
    ["Streaming margins have years of headroom to peer levels"],
    ["Moderate recovery (~15%/yr implied) — achievable against +21% OI delivery"],
    ["Earnings 2026-11-12", "Streaming margin disclosure"],
    ["Parks carry the profit pool into any consumer downturn",
     "Charge-driven EPS noise obscures the improvement"],
    ["Streaming margin below 5%", "Parks OI negative YoY", "OI growth below 8%"],
    "Five years of transition disappointments earn skepticism: OI growth "
    "must survive a consumer cycle with $42B of debt before the franchise "
    "multiple returns.",
    ["Base case assumes parks hold through softness"],
    ["Content-charge noise in EPS (stated; OI is the honest lens)"],
    ["None at 21x with +21% OI delivery"],
    ["Segment margins post-reorg (pending)"],
    "ATTRACTIVE", "MEDIUM", 61,
    [C("FY2026-Q3 operating income grew 21.4% TTM YoY on +6.8% revenue.",
       "FACT", "SEC:ACCESSION:0001744489-26-000057"),
     C("The last three surprises were +3.8%, +5.4%, +10.8%; one insider discretionary purchase stands.",
       "FACT", "FMP:EARNINGS:DIS", "SEC:FORM4:DIS"),
     C("The streaming-leverage inflection is now the P&L's dominant driver.",
       "INFERENCE", "SEC:ACCESSION:0001744489-26-000057"),
     C("Probability-weighted 12-month value is ~+9% above the current price.",
       "FORECAST")])

REPASS["MCD"] = S(
    "Post-FY26-Q2 re-pass with REPAIRED DATA: the EPS units defect is "
    "fixed — verified TTM EPS $12.36, P/E 22.2 at the 15th percentile. "
    "Revenue +3.7% with the 46.2% margin machine intact. The implied "
    "requirement eased to 18.2%/yr; still a premium to the 5.1% delivered. "
    "WATCH stands on cleaner numbers.",
    "STABLE", "MODERATE",
    ["Verified P/E 22.2 (15th percentile) after the data fix — cheaper than the corrupt data suggested",
     "Operating margin 46.2%; franchise royalty economics intact"],
    ["Revenue +3.7% — the value-menu traffic war is a real drag",
     "$38.9B net debt services the buyback"],
    ["Traffic recovery at low-income cohorts", "International franchise growth"],
    sc((11.5, 17.0), (12.8, 21.0), (14.0, 25.0), 0.25, 0.55, 0.20),
    "With honest numbers, MCD is a fairly-priced royalty: 22x for "
    "GDP-plus growth and fortress margins. The 13pt premium of implied "
    "over delivered growth is the quality tax, now at the low end of its "
    "own range.",
    ["Pricing power plus digital mix can outrun traffic softness for years"],
    ["High-teens implied growth vs ~5% delivered — a smaller stretch than before"],
    ["Next earnings", "Value-platform traffic data"],
    ["GLP-1 volume pressure is slow but structural", "Franchisee margin strain from value pricing"],
    ["Comp traffic negative two quarters", "Operating margin below 44%"],
    "A royalty on food volume in a GLP-1 world at a growth premium is "
    "quietly fighting its own denominator; fair price, structural "
    "headwind.",
    ["FCF-based checks retired now that EPS is verified"],
    ["EPS defect FIXED and verified against raw facts (was a units bug, now guarded by tests)"],
    ["22x at the 15th percentile is fair, not cheap"],
    ["None material"],
    "WATCH", "MEDIUM", 53,
    [C("Verified TTM diluted EPS is $12.36 (P/E 22.2, 15th percentile) after the units-defect repair.",
       "FACT", "SEC:ACCESSION:0000063908-26-000073", "SM:DATA_QUALITY:MCD"),
     C("FY2026-Q2 revenue grew 3.7% with a 46.2% operating margin.",
       "FACT", "SEC:ACCESSION:0000063908-26-000073"),
     C("The quality premium (18.2%/yr implied vs 5.1% delivered) sits at the low end of its historical range.",
       "INFERENCE", "YAHOO:CHART:MCD"),
     C("Probability-weighted 12-month value is ~+2% above the current price.",
       "FORECAST")])

REPASS["QCOM"] = S(
    "Post-FY26-Q3 re-pass: the Apple-insourcing cliff is now visible in "
    "the numbers — revenue -4.0% and operating income -41.1% YoY. The "
    "market shrugged (+16% twelve-month) on auto/IoT hopes. The "
    "fairly-priced thesis survives, barely: 19.4x on declining earnings "
    "with a 5.9% FCF yield. WATCH with the negative tilt now earned.",
    "DETERIORATING", "MODERATE",
    ["FCF $10.4B (5.9% yield) still funds the wait",
     "Auto design-win pipeline keeps scaling off-stage"],
    ["Operating income -41.1% YoY — the licensing/modem cliff arrived",
     "Revenue -4.0% against a rising market"],
    ["Auto/IoT revenue reaching disclosure scale", "Licensing renewal economics"],
    sc((7.0, 12.0), (8.5, 15.0), (10.0, 19.0), 0.30, 0.50, 0.20),
    "July's 'correctly priced' read understated the speed of the cliff: a "
    "-41% OI print is not zero-expectations territory. The diversification "
    "option is real but now must outrun visible decay.",
    ["Auto backlog conversion could offset the handset cliff within two years"],
    ["A soft landing the latest OI print questions"],
    ["Next earnings", "Auto revenue milestones"],
    ["Further Apple volume loss on a known schedule", "Android premium-tier softness"],
    ["OI declining >30% again", "Auto revenue growth below 20%"],
    "The cliff is not priced as a cliff: 19x earnings assumes the decline "
    "is a dip, and the -41% OI print says otherwise.",
    ["Scenario EPS assumes stabilization mid-cliff"],
    ["None material"],
    ["19.4x for negative growth is only cheap if the trough is now"],
    ["Licensing vs chip margins (pending)"],
    "WATCH", "LOW", 45,
    [C("FY2026-Q3 revenue fell 4.0% and operating income fell 41.1% YoY.",
       "FACT", "SEC:ACCESSION:0000804328-26-000086"),
     C("TTM FCF is $10.4B (5.9% yield) at 19.4x earnings.",
       "FACT", "SEC:ACCESSION:0000804328-26-000086", "YAHOO:CHART:QCOM"),
     C("The insourcing cliff is visible in filings and not fully priced.",
       "INFERENCE", "SEC:ACCESSION:0000804328-26-000086"),
     C("Probability-weighted 12-month value is ~7% below the current price.",
       "FORECAST")])

REPASS["RIVN"] = S(
    "Post-FY26-Q2 re-pass: gross margin extended to +7.5% (from the "
    "just-positive threshold), revenue +27.2%, losses narrowing (OI trend "
    "+25.0% YoY improvement), and BOTH insiders' buys stand. Still a "
    "financing binary — burn is -$3.5B/yr — but every monitored metric "
    "moved the right way. WATCH holds with improved skew.",
    "IMPROVING", "WEAK",
    ["Gross margin +7.5% — two consecutive positive quarters, trend confirmed",
     "Revenue +27.2% YoY; beats +23.8%, +13.0%, +29.1%"],
    ["FCF -$3.5B/yr — runway remains the dominant variable",
     "Receivables gap 18.4pt worth watching in a scale-up"],
    ["R2 ramp economics", "Capital-raise timing vs rate environment"],
    sc(7.0, 17.0, 38.0, 0.35, 0.40, 0.25),
    "The operational half of the binary keeps landing green: margins, "
    "growth, beats, insider conviction. The financing half is untouched "
    "— the burn requires open capital markets into 2028. Improved odds, "
    "same structure.",
    ["The insiders buying the R2 cost curve keep being right"],
    ["Partial success is in the price; the tails are not"],
    ["Earnings 2026-11-03", "R2 rate milestones"],
    ["A funding window slam repricing everything", "EV price war round two"],
    ["Gross margin back below 3%", "A raise below market", "Receivables gap above 25pt"],
    "Positive gross margin at -60% operating margin means the factory "
    "works and the company still doesn't; the equity remains a leveraged "
    "call on 2027-28 capital-market weather.",
    ["All values remain capital-structure-dependent judgment"],
    ["Receivables 18.4pt gap — new watch item this quarter"],
    ["EV/revenue 3.5 for improving-but-negative operations"],
    ["Per-platform unit economics (not disclosed)"],
    "WATCH", "LOW", 46,
    [C("FY2026-Q2 gross margin reached +7.5% with revenue +27.2% YoY; operating losses narrowed.",
       "FACT", "SEC:ACCESSION:0001874178-26-000054"),
     C("Both insider discretionary purchases stand; beats ran +23.8%, +13.0%, +29.1%.",
       "FACT", "SEC:FORM4:RIVN", "FMP:EARNINGS:RIVN"),
     C("Operational execution keeps improving; the financing binary is unchanged.",
       "INFERENCE", "SEC:ACCESSION:0001874178-26-000054"),
     C("Scenario dispersion remains the universe's widest; conviction structurally LOW.",
       "FORECAST")])

REPASS["VZ"] = S(
    "Post-FY26-Q2 re-pass: revenue slipped negative (-0.7%) and operating "
    "income fell 12.2% — the wrong direction for the flat-is-fine thesis — "
    "while the FCF tag gap persists a second quarter (still unverifiable). "
    "Beats continue against low bars. WATCH holds with less patience.",
    "DETERIORATING", "WEAK",
    ["Beats persist: +3.8%, +4.9%, +2.4%",
     "P/E 12.1 with the dividend intact"],
    ["Operating income -12.2% YoY — promo costs are biting",
     "FCF STILL unverifiable (second consecutive tag gap); 85th-percentile P/E vs own history"],
    ["Fixed-wireless net adds vs promo cost", "Data repair for cash verification"],
    sc((3.6, 8.0), (4.0, 11.0), (4.4, 13.0), 0.30, 0.50, 0.20),
    "The value case thins each quarter: declining OI, invisible cash flow, "
    "and a multiple at its own 85th percentile despite the low absolute "
    "level. T remains the verified version of this trade.",
    ["Fixed-wireless could still bend the growth line"],
    ["Perpetual decline — but OI is now actually declining"],
    ["Earnings 2026-10-20", "VZ cash-flow tag fix (queued)"],
    ["Promo escalation into a saturated market", "Leverage invisible to our tags"],
    ["OI declining >10% again", "Broadband adds negative", "Churn above 1.1%"],
    "Cheap-telecom theses die from exactly this pattern: small revenue "
    "declines, margin erosion from defensive promos, and a dividend that "
    "gradually becomes the only argument.",
    ["Scenario multiples assume the decline stays shallow"],
    ["FCF unverifiable two quarters running — the cash case rests on faith"],
    ["85th-percentile vs own history despite 12.1x absolute"],
    ["Debt + cash-flow tag mapping for telecoms (queued)"],
    "WATCH", "LOW", 44,
    [C("FY2026-Q2 revenue fell 0.7% and operating income fell 12.2% YoY.",
       "FACT", "SEC:ACCESSION:0000732712-26-000046"),
     C("Cash-flow fields remain missing from the normalization — FCF unverified for a second quarter.",
       "FACT", "SM:DATA_QUALITY:VZ"),
     C("The value thesis is thinning: declining income, unverifiable cash, relative-history-rich multiple.",
       "INFERENCE", "YAHOO:CHART:VZ"),
     C("Probability-weighted 12-month value is ~4% below the current price.",
       "FORECAST")])

REPASS["KLAC"] = S(
    "Re-pass with an honest abstention renewed: KLAC's market cap remains "
    "WITHHELD by the reconciliation guard (cover-page vs diluted-share "
    "bases still disagree ~10x around the 2026 split) and the FY26-Q4 "
    "filing left operating income untagged in our normalization. Revenue "
    "+15.2% is real; nearly every valuation metric is not computable. "
    "Classification: INSUFFICIENT_DATA — scenario values are indicative "
    "shapes only, not a priced view.",
    "IMPROVING", "MODERATE",
    ["Revenue +15.2% YoY; FCF $3.8B on the fixed-scale basis",
     "Semicap cycle tailwinds shared with AMAT/LRCX"],
    ["Market cap withheld (share-basis reconciliation fails ~10x)",
     "Operating income missing from the latest normalization"],
    ["Data repair completing", "Memory-equipment cycle"],
    sc(150.0, 210.0, 290.0, 0.30, 0.45, 0.25),
    "The machine refuses a view it cannot price: with market cap withheld "
    "and OI untagged, every multiple is unreliable. The cycle context "
    "(AMAT/LRCX at UNATTRACTIVE) likely applies, but asserting it without "
    "computable valuation would be narrative.",
    ["The data fix could reveal either a cheap or an expensive stock — genuinely unknown"],
    ["Unknown — that is the point"],
    ["Share-basis reconciliation fix", "Next 10-K share detail"],
    ["Cycle-peak risk by analogy to semicap peers"],
    ["N/A until data repairs — no falsifiable thesis is issued"],
    "Issuing a classification on unreconciled data would be exactly the "
    "false precision this system exists to prevent.",
    ["Scenario values are placeholders for chart continuity, stated as such"],
    ["Market-cap withheld; OI tag gap; EPS split-basis inconsistent"],
    ["Not computable this quarter"],
    ["KLAC share-class/split reconciliation (data fix queued)"],
    "INSUFFICIENT_DATA", "LOW", None,
    [C("FY2026-Q4 revenue grew 15.2% YoY; TTM FCF is $3.8B.",
       "FACT", "SEC:ACCESSION:0000319201-26-000027"),
     C("Market cap is withheld by the reconciliation guard; operating income is untagged in the latest normalization.",
       "FACT", "SM:DATA_QUALITY:KLAC"),
     C("No priced view is issued: valuation metrics are not computable on unreconciled data.",
       "INFERENCE", "SM:DATA_QUALITY:KLAC"),
     C("Scenario values are indicative shapes only, pending the data repair.",
       "FORECAST")])
