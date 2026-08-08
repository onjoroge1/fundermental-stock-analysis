"""Analyst specs batch 4: telecom/cable, airlines, media, and (after the
bank adapter v1 landed) SOFI. Also carries post-data-fix revisions of
earlier specs — merge order lets this batch override the previous ones."""

from specs_extra import S, C, sc

SPECS_EXTRA3 = {}

SPECS_EXTRA3["T"] = S(
    "Telecom cash machine at 7.8× earnings and a 10.7% FCF yield, priced "
    "for a -10.1%/yr decline while delivering +1.3% growth and beating "
    "every recent quarter (+12.4%, +3.3%, +10.2%). DATA CAVEAT: our "
    "normalized net-debt figure materially understates AT&T's actual "
    "leverage (partial debt-tag coverage) — EV-based metrics are distrusted "
    "here; equity-based P/E and FCF yield are the anchors.",
    "STABLE", "MODERATE",
    ["FCF $17.6B (13.9% margin), fiber subscriber growth steady",
     "Expectations score 91.5: consistent beats against low expectations"],
    ["Revenue growth ~2% is the ceiling absent fiber acceleration",
     "True leverage (understated in our tags) is the structural constraint"],
    ["Fiber penetration pace", "Capital returns after deleveraging targets"],
    sc((2.8, 6.0), (3.2, 9.0), (3.5, 11.0), 0.25, 0.55, 0.20),
    "Deep-value income: the price implies decline that five consecutive "
    "beat quarters contradict. At a 10.7% FCF yield the dividend plus "
    "modest re-rating carries double-digit expected returns without "
    "requiring growth.",
    ["Fiber + mobility bundling is quietly improving the growth mix"],
    ["A perpetual -10%/yr decline the operating results contradict"],
    ["Earnings 2026-10-28", "Deleveraging milestone announcements"],
    ["Actual debt load (understated in our data) limits downside protection in a rate spike",
     "Fiber capex cycle could extend beyond guidance"],
    ["FCF below $14B TTM", "Postpaid phone churn above 1%", "Fiber net-adds decelerating two quarters"],
    "Telecom terminal value is the bear case: fixed-line decay, fiber "
    "overbuild competition, and a debt load our own data cannot fully see. "
    "Cheap telecom has been a value trap for a decade of rate regimes.",
    ["Base multiple 9× assumes rate stability",
     "Debt-tag gap means leverage risk is assessed from outside data, not our normalization"],
    ["Net-debt understatement flagged — do not trust our EV/leverage ratios for this name"],
    ["None on equity basis — 7.8× with beats is genuinely cheap"],
    ["Full debt-instrument tag mapping for telecoms (data fix pending)"],
    "ATTRACTIVE", "MEDIUM", 62,
    [C("TTM FCF is $17.6B (10.7% yield); the shares trade at 7.8× earnings.",
       "FACT", "SEC:ACCESSION:0000732717-26-000297"),
     C("Price-implied growth is -10.1%/yr vs +1.3% delivered; last three surprises +12.4%, +3.3%, +10.2%.",
       "FACT", "YAHOO:CHART:T", "FMP:EARNINGS:T"),
     C("Our normalized net debt materially understates actual leverage (debt-tag coverage gap).",
       "FACT", "SM:DATA_QUALITY:T"),
     C("Probability-weighted 12-month value is ~+15% above the current price.",
       "FORECAST")])

SPECS_EXTRA3["VZ"] = S(
    "Telecom incumbent at 11.3× earnings, priced for -11.1%/yr against "
    "+0.3% delivered, with steady beats. TWO DATA CAVEATS: cash-flow "
    "statement fields are missing from the current normalization (no FCF "
    "figure), and net debt is materially understated by tag coverage — "
    "both stated, neither guessed.",
    "STABLE", "WEAK",
    ["Beats: +3.8%, +4.9%, +2.4%; expectations score 83",
     "P/E 11.3 with a covered dividend"],
    ["Growth ceiling ~2-3%; wireless market fully penetrated",
     "FCF unverifiable in our data this quarter — quality checks limited"],
    ["Broadband/fixed-wireless net adds", "Promotional intensity in wireless"],
    sc((3.8, 8.0), (4.2, 11.0), (4.6, 13.0), 0.25, 0.55, 0.20),
    "Similar shape to T at a slightly richer multiple with weaker data "
    "verification: the priced-in decline is contradicted by results, but "
    "with FCF unverified this quarter, conviction stays lower than T's.",
    ["Fixed-wireless broadband is a genuine share-take engine"],
    ["Perpetual decline at -11%/yr"],
    ["Earnings 2026-10-20", "Broadband net-add prints"],
    ["Promo wars compress wireless ARPU", "True leverage not visible in our tags"],
    ["Postpaid churn above 1.1%", "Broadband net adds negative", "Dividend coverage deteriorating on restored FCF data"],
    "VZ has under-grown T's results with a higher multiple and needed more "
    "promo spend to hold share; without verified FCF our value case rests "
    "on earnings alone, and levered telecom earnings deserve skepticism.",
    ["Scenario multiples assume rate stability", "No FCF verification this quarter"],
    ["Missing cash-flow fields + understated debt — two open data gaps"],
    ["11.3× is cheap absolutely but rich vs T on every verified metric"],
    ["VZ cash-flow tag mapping (data fix pending)"],
    "WATCH", "LOW", 52,
    [C("The shares trade at 11.3× earnings; last three surprises were +3.8%, +4.9%, +2.4%.",
       "FACT", "SEC:ACCESSION:0000732712-26-000023", "FMP:EARNINGS:VZ"),
     C("Price-implied growth is -11.1%/yr vs +0.3% delivered.",
       "FACT", "YAHOO:CHART:VZ"),
     C("Cash-flow fields are missing and net debt is understated in our normalization — both flagged.",
       "FACT", "SM:DATA_QUALITY:VZ"),
     C("Probability-weighted 12-month value is roughly at the current price; T offers the same thesis cheaper.",
       "FORECAST")])

SPECS_EXTRA3["TMUS"] = S(
    "The share-taking telecom de-rated 24.7% to 10.5× FCF: revenue +7.9% "
    "(the only growing large carrier), FCF $18.4B at a 20.0% margin, price "
    "implying -6.9%/yr against +3.5% delivered. One insider bought; one "
    "large sale ($148M) offsets.",
    "IMPROVING", "MODERATE",
    ["Revenue +7.9% YoY — growth leadership among carriers",
     "FCF margin 20.0%; buybacks running at scale"],
    ["P/E percentile 5th vs own history reflects the de-rating, not deterioration",
     "Receivables +6.3pt vs revenue (device financing mix)"],
    ["Fiber JV scaling", "Postpaid share gains vs saturated market"],
    sc(120.0, 210.0, 285.0, 0.25, 0.55, 0.20),
    "The best operating story in telecom at its cheapest-ever FCF multiple: "
    "-25% in a year while growing share and cash flow. The priced-in "
    "decline (-6.9%/yr) is inconsistent with the only carrier that is "
    "actually growing.",
    ["Saturation fear ignores TMUS's structural share-take from cable and legacy carriers"],
    ["Growth ending: -6.9%/yr implied for the growth leader"],
    ["Quarterly postpaid adds", "Buyback pace at depressed multiple"],
    ["Industry promo escalation compressing everyone's economics",
     "Spectrum/capex cycle re-acceleration"],
    ["Postpaid phone net adds below 500k/quarter", "FCF margin below 16%",
     "Service revenue growth below 3%"],
    "Telecom is a mature oligopoly: TMUS's growth is share-take, which "
    "ends, and the de-rating may be the market correctly pricing "
    "convergence toward industry growth (~0%). The $148M insider sale "
    "argues the smart money sees the ceiling.",
    ["Fair values are FCF-multiple judgments; device-financing receivables complicate FCF quality"],
    ["Receivables gap modest but persistent (financing mix)"],
    ["None at 10.5× FCF — the multiple already discounts convergence"],
    ["Fiber JV economics (pending)"],
    "ATTRACTIVE", "MEDIUM", 63,
    [C("Revenue grew 7.9% YoY with $18.4B TTM FCF (20.0% margin); the stock fell 24.7% in twelve months.",
       "FACT", "SEC:ACCESSION:0001283699-26-000101", "YAHOO:CHART:TMUS"),
     C("Price-implied growth is -6.9%/yr for the only growing large carrier.",
       "FACT", "YAHOO:CHART:TMUS"),
     C("The de-rating reflects saturation fear, not delivered deterioration.",
       "INFERENCE", "SEC:ACCESSION:0001283699-26-000101"),
     C("Probability-weighted 12-month value is ~+12% above the current price.",
       "FORECAST")])

SPECS_EXTRA3["CMCSA"] = S(
    "Cable incumbent in structural broadband share loss, priced at 7.1× "
    "earnings after a 25.9% decline. DATA CAVEAT UP FRONT: our market-cap "
    "reconciliation for CMCSA looks wrong (share-count coverage), so the "
    "eye-popping 44% FCF yield is NOT trusted; the equity P/E of 7.1 is "
    "the only anchor used. Revenue -1.2%, operating income -13.9%.",
    "DETERIORATING", "MODERATE",
    ["$20.4B FCF supports whatever the true equity value is",
     "Theme parks and Peacock narrowing losses"],
    ["Broadband subscriber losses to fiber and fixed wireless — the core is eroding",
     "Operating income -13.9% YoY"],
    ["Broadband sub trajectory", "Content/parks cyclicality"],
    sc((2.8, 6.0), (3.1, 8.5), (3.4, 11.0), 0.30, 0.50, 0.20),
    "Deep value with a deteriorating core and a data asterisk: at 7× "
    "earnings the market prices permanent decline (-38%/yr implied on our "
    "distrusted EV basis; still deeply negative on any basis). The "
    "question is decay rate vs cash-return rate — but our share-count "
    "issue caps conviction at LOW until reconciled.",
    ["Convergence bundling could stabilize broadband churn"],
    ["Rapid terminal decline of cable broadband"],
    ["Quarterly broadband net losses", "Parks booking trends"],
    ["Fiber overbuild accelerating into CMCSA footprints",
     "Media segment secular pressure"],
    ["Broadband losses above 300k/quarter", "OI decline worse than -15% again"],
    "Cable's terminal phase can be fast: broadband was the profit pool "
    "funding everything else, and both fixed wireless and fiber are taking "
    "it simultaneously. 7× earnings for a melting core is fair, not cheap. "
    "Our own market-cap doubt cuts both ways.",
    ["Share-count reconciliation unresolved — mcap-based metrics unusable",
     "Base case assumes decay stays gradual"],
    ["Market-cap/share-count reconciliation flagged (like KLAC) — queued for the split-check fix"],
    ["P/E 7.1 is the only trusted valuation metric this quarter"],
    ["Share-class mapping for CMCSA (data fix pending)"],
    "WATCH", "LOW", 48,
    [C("Revenue declined 1.2% YoY with operating income -13.9%; the shares trade at 7.1× earnings.",
       "FACT", "SEC:ACCESSION:0001628280-26-049360"),
     C("Our market-cap reconciliation for CMCSA is suspect (share-count coverage) — EV/FCF-yield metrics are not trusted.",
       "FACT", "SM:DATA_QUALITY:CMCSA"),
     C("The core broadband franchise is in structural share loss.",
       "INFERENCE", "SEC:ACCESSION:0001628280-26-049360"),
     C("Probability-weighted 12-month value is ~+15% above price, with conviction capped by the data caveat.",
       "FORECAST")])

SPECS_EXTRA3["DAL"] = S(
    "Best-in-class legacy airline after a +57.3% year: revenue +18.7%, "
    "but operating income -11.3% (cost pressure) and the P/E (14.2×) now "
    "sits above the airline historical band. Beats persist (+1.3%, "
    "+10.3%, +64.4% — last one a low-base artifact). Cash-flow fields "
    "missing this normalization (no FCF figure).",
    "MIXED", "MODERATE",
    ["Revenue +18.7% YoY; premium-cabin mix structurally improving",
     "Loyalty/AmEx remuneration is a quasi-subscription stream"],
    ["Operating income -11.3% — costs are outrunning the revenue boom",
     "FCF unverifiable this quarter (missing cash-flow tags)"],
    ["Premium/loyalty mix durability through a downturn", "Fuel and labor cost path"],
    sc((5.2, 9.0), (6.3, 12.0), (7.3, 15.0), 0.30, 0.50, 0.20),
    "The best operator in a bad industry, fully priced for it: at 14× "
    "post-run, the multiple assumes premium mix has permanently "
    "de-cyclicalized the business — a thesis the -11% OI print already "
    "strains. Airline discipline says take the priced compliment and pass.",
    ["If premium/loyalty revenue truly is structural, airline multiples re-rate industry-wide"],
    ["A de-cyclicalized Delta at a multiple the industry has never sustained"],
    ["Earnings 2026-10-08", "Fuel curve"],
    ["Airlines remain leveraged fuel-and-GDP derivatives — one demand shock resets everything",
     "OI already declining while the stock re-rated +57%"],
    ["Operating margin below 6%", "Premium revenue growth below total revenue growth",
     "Fuel spike above $110/bbl equivalent"],
    "Every cycle, the market decides the best airline has escaped the "
    "industry; every cycle it hasn't. OI is already falling at the revenue "
    "peak — the +57% year bought a cyclical at 14× with costs inflecting. "
    "The missing FCF data removes the one metric that could defend it.",
    ["Scenario multiples (9-15×) are themselves generous vs airline history"],
    ["Cash-flow fields missing — FCF unverified (airline tag mapping gap)"],
    ["14.2× at 68th percentile vs own history, post-run, with declining OI"],
    ["Airline cash-flow tag mapping (data fix pending)"],
    "WATCH", "LOW", 44,
    [C("Revenue grew 18.7% YoY while operating income fell 11.3%; the stock returned +57.3%.",
       "FACT", "SEC:ACCESSION:0000027904-26-000031", "YAHOO:CHART:DAL"),
     C("Cash-flow statement fields are missing from the current normalization — FCF unverified.",
       "FACT", "SM:DATA_QUALITY:DAL"),
     C("The multiple now embeds structural de-cyclicalization the OI trend contradicts.",
       "INFERENCE", "YAHOO:CHART:DAL"),
     C("Probability-weighted 12-month value is ~13% below the current price.",
       "FORECAST")])

SPECS_EXTRA3["UAL"] = S(
    "Legacy airline at 11.1× earnings with the price implying -11.0%/yr "
    "against +9.5% delivered — the only airline with a negative "
    "expectations gap. Beats steady (+5.8%, +10.2%, +5.9%); base rates "
    "favorable (66.7% outperformed). Net-debt figure distrusted (airline "
    "debt/lease tags).",
    "MIXED", "MODERATE",
    ["Revenue +16.0% YoY, 3-yr CAGR +9.5% — best growth among legacies",
     "Expectations score 91.7 with favorable base rates"],
    ["Operating income -17.3% — same cost squeeze as DAL",
     "Leverage understated in our tags (lease/debt coverage)"],
    ["International premium demand durability", "Cost convergence vs DAL"],
    sc((9.0, 7.0), (11.0, 10.0), (13.0, 13.0), 0.30, 0.50, 0.20),
    "The cheaper way to own the premium-travel thesis: UAL delivers "
    "DAL-adjacent growth at 3 fewer turns of P/E with expectations still "
    "negative. Cyclical discipline still applies — this is relative value "
    "within a leveraged industry, not absolute safety.",
    ["Premiumization at UAL is earlier-cycle than DAL's — more runway if it persists"],
    ["Decline: -11%/yr implied vs +9.5% delivered"],
    ["Earnings 2026-10-21", "Transatlantic booking curves"],
    ["Same fuel/GDP leverage as every airline",
     "OI declining into the re-rated price"],
    ["Operating margin below 5%", "International RASM negative two quarters"],
    "An 11× airline at cycle peak is not cheap — it is normal; the "
    "'negative expectations gap' partly reflects the market correctly "
    "refusing to capitalize peak cycle earnings. Leverage our tags cannot "
    "fully see decides the downside case.",
    ["Base rates derive from a survivorship-biased panel",
     "Reverse-DCF basis (NOPAT proxy) flatters an airline's true cash conversion"],
    ["Debt/lease tag coverage understates leverage"],
    ["None at 11× — but airlines at cycle peaks routinely see multiples halve"],
    ["Lease-liability mapping for airlines (pending)"],
    "WATCH", "MEDIUM", 54,
    [C("Revenue grew 16.0% YoY (3-yr CAGR +9.5%); the shares trade at 11.1× earnings.",
       "FACT", "SEC:ACCESSION:0000100517-26-000139"),
     C("Price-implied growth is -11.0%/yr — the only negative airline expectations gap.",
       "FACT", "YAHOO:CHART:UAL"),
     C("Operating income fell 17.3% — cost pressure is industry-wide, not DAL-specific.",
       "FACT", "SEC:ACCESSION:0000100517-26-000091"),
     C("Probability-weighted 12-month value is ~9% below the current price; relative value only.",
       "FORECAST")])

SPECS_EXTRA3["AAL"] = S(
    "The leveraged laggard of the airline recovery: negative TTM EPS "
    "(-$0.49), 1.7% operating margin, $21.5B net debt on a $10B market "
    "cap, and a reverse DCF requiring +55.3%/yr — an impossible bar for "
    "an airline. OCF/NI of -13.4 flags severe earnings-cash divergence.",
    "MIXED", "WEAK",
    ["Revenue +16.3% YoY riding the same demand wave",
     "Beats vs deeply depressed estimates (+13.7%, +400% off near-zero bases)"],
    ["Operating income -60.7%; still lossmaking on TTM EPS",
     "2× market-cap net debt makes the equity an option on the cycle"],
    ["Debt paydown pace vs cycle length", "Premium mix catch-up attempts"],
    sc(8.0, 13.0, 20.0, 0.35, 0.45, 0.20),
    "AAL's equity is a call option on the airline cycle staying strong "
    "long enough to deleverage — with the price already requiring 55%/yr "
    "growth. The two better-capitalized legacies offer the same upside "
    "without the balance-sheet cliff.",
    ["Maximum operating leverage TO the cycle if demand runs hot for years"],
    ["A perfect, extended cycle: +55%/yr implied"],
    ["Earnings 2026-10-22", "Debt maturity refinancings"],
    ["First demand wobble hits the most leveraged name hardest",
     "Surprise metrics meaningless off near-zero bases (discounted analytically)"],
    ["Operating margin below 1%", "Any debt-refinancing spread above 8%",
     "Revenue growth below 8%"],
    "Negative earnings, 2× leverage, and the industry's weakest premium "
    "franchise, priced for the fastest growth requirement in the sector: "
    "the option-like upside is real, but the base case is permanent "
    "equity-holder dilution by the balance sheet.",
    ["All scenario values are equity-option judgments dominated by the debt stack"],
    ["OCF/NI -13.4: reported losses vs positive cash flow needs decomposition"],
    ["+55%/yr implied is unachievable outside hyper-cyclical snapbacks"],
    ["Lease/debt maturity ladder (not parsed)"],
    "UNATTRACTIVE", "MEDIUM", 32,
    [C("TTM diluted EPS is -$0.49 with a 1.7% operating margin; net debt is $21.5B vs a $10B market cap.",
       "FACT", "SEC:ACCESSION:0000006201-26-000052"),
     C("Price-implied growth is +55.3%/yr — the highest requirement in the expanded universe.",
       "FACT", "YAHOO:CHART:AAL"),
     C("The equity is an option on an extended cycle; the debt owns the base case.",
       "INFERENCE", "SEC:ACCESSION:0000006201-26-000052"),
     C("Probability-weighted 12-month value is ~13% below the current price.",
       "FORECAST")])

SPECS_EXTRA3["LUV"] = S(
    "The premium-priced discount airline: 26.6× earnings — double the "
    "legacy carriers — for a 3.5% operating margin, negative TTM FCF, and "
    "receivables growing 13.6pt faster than revenue. The business-model "
    "transition (bags, seating) is priced as already successful.",
    "MIXED", "WEAK",
    ["Revenue +16.4% YoY; operating income +26.7% off a low base",
     "Balance sheet remains the industry's cleanest (net cash on our tags)"],
    ["Negative FCF (-$0.5B); margins a third of DAL's",
     "26.6× P/E is the airline sector's highest by far"],
    ["Seating/bag-fee monetization ramp", "Cost program delivery"],
    sc((1.5, 12.0), (2.2, 15.0), (2.8, 18.0), 0.30, 0.50, 0.20),
    "Paying the sector's highest multiple for its thinnest margins "
    "requires the model transition to work perfectly and immediately; "
    "recent activist-driven changes are unproven, and the receivables "
    "build plus negative FCF say the P&L improvement is not yet cash.",
    ["If monetization converges LUV toward legacy unit revenue, EPS could double"],
    ["A completed, successful business-model transition"],
    ["Next earnings (date unavailable)", "Monetization metric disclosure"],
    ["Transition alienating the loyalty base is a real франchise risk",
     "Negative FCF during the transition removes the safety net"],
    ["Operating margin below 2%", "Receivables gap above 20pt", "FCF negative for two more quarters"],
    "LUV trades at growth-stock multiples on turnaround hope while "
    "generating no cash: every airline that traded above 20× regretted "
    "it within two years. The 13.6pt receivables gap during a model "
    "transition deserves particular suspicion.",
    ["Base case assumes fee monetization hits plan in year one"],
    ["Receivables +13.6pt vs revenue; FCF negative"],
    ["26.6× — double the sector — for the sector's worst margins"],
    ["Monetization program economics (pending transcripts)"],
    "UNATTRACTIVE", "MEDIUM", 33,
    [C("The shares trade at 26.6× earnings — the airline sector's highest — with a 3.5% operating margin and negative TTM FCF.",
       "FACT", "SEC:ACCESSION:0000092380-26-000077"),
     C("Receivables grew 13.6 points faster than revenue.",
       "FACT", "SEC:ACCESSION:0000092380-26-000077"),
     C("The model transition is priced as accomplished before generating cash.",
       "INFERENCE", "YAHOO:CHART:LUV"),
     C("Probability-weighted 12-month value is ~29% below the current price.",
       "FORECAST")])

SPECS_EXTRA3["DIS"] = S(
    "Media conglomerate at 15.0× earnings (5th percentile of its own "
    "history) after a -20.8% year: streaming now profitable, parks "
    "steady, beats consistent (+5.7%, +3.8%, +5.4%), and ONE insider "
    "bought discretionarily. The FCF-based reverse DCF (+17.3%/yr) "
    "overstates the requirement because content amortization depresses "
    "reported FCF; the earnings basis is the fairer read.",
    "STABLE", "MODERATE",
    ["Streaming segment profitability inflected; beats consistent",
     "P/E at the 5th percentile of five-year history; insider buying"],
    ["$41.7B net debt; linear-TV decay continues inside the averages",
     "FCF ($7.1B) lags earnings on content-spend timing"],
    ["Streaming margin scaling toward peers", "Parks demand through the consumer cycle"],
    sc((5.8, 11.0), (6.8, 15.0), (7.6, 19.0), 0.25, 0.55, 0.20),
    "The de-rating has finally reached value territory for the only media "
    "franchise with both scaled streaming and irreplaceable physical "
    "assets. At 15× trough-transition earnings with beats running and an "
    "insider buying, the risk/reward tilts positive; linear decay and "
    "leverage keep it from ATTRACTIVE outright — a strong WATCH.",
    ["Streaming profitability is compounding while the multiple still prices linear decay"],
    ["Continued linear-TV erosion swallowing streaming gains"],
    ["Earnings 2026-08-05", "Streaming margin disclosure; parks bookings"],
    ["Consumer downturn hits parks (the profit pool) with high operating leverage",
     "Content-cost inflation in the streaming wars redux"],
    ["Streaming margin regressing below 5%", "Parks operating income negative YoY",
     "Leverage above 3× on restored EBITDA basis"],
    "DIS has been a 'cheap transition story' for five straight years while "
    "earning less each year than the year before the transition began; "
    "15× may simply be the correct multiple for a structurally smaller "
    "earnings base with $42B of debt.",
    ["Base case assumes parks hold through any consumer softness",
     "FCF basis and earnings basis disagree — earnings basis chosen, stated"],
    ["Content amortization vs cash spend timing complicates FCF reads"],
    ["None at the 5th percentile of own history"],
    ["Segment economics post-reorganization (pending)"],
    "WATCH", "MEDIUM", 58,
    [C("The shares trade at 15.0× earnings — the 5th percentile of five-year history — after a 20.8% decline.",
       "FACT", "YAHOO:CHART:DIS"),
     C("Last three surprises: +5.7%, +3.8%, +5.4%; one insider made a discretionary purchase.",
       "FACT", "FMP:EARNINGS:DIS", "SEC:FORM4:DIS"),
     C("Streaming profitability inflection is not yet reflected in the multiple.",
       "INFERENCE", "SEC:ACCESSION:0001744489-26-000037"),
     C("Probability-weighted 12-month value is ~+6% above the current price.",
       "FORECAST")])


SPECS_EXTRA3["KLAC"] = S(
    "Semicap process-control leader, analyzable at last after the split "
    "data-fix (10:1 June 2026 split now reconciled; market cap $275B "
    "verified against both share bases). Same cycle-peak shape as "
    "AMAT/LRCX: +134.9% year, 59.4x earnings at the 100th percentile, "
    "price requiring 41.4%/yr vs 9.7% delivered. Operating-income subtotal "
    "untagged (margin structure partially unverifiable).",
    "IMPROVING", "MODERATE",
    ["Revenue +11.5% YoY; process control is the highest-moat semicap niche",
     "FCF margin 30.7% -- best cash conversion in equipment"],
    ["Delivered 3-year CAGR (9.7%) is a quarter of what the price requires",
     "Two insiders sold ($1.6M); zero buys"],
    ["WFE cycle depth", "Process-control intensity per node shrink"],
    sc((3.0, 30.0), (3.8, 36.0), (4.6, 44.0), 0.30, 0.50, 0.20),
    "The best business in semicap at the sector's most extreme relative "
    "price: 59x earnings, 21.3x EV/revenue, 100th-percentile valuation, "
    "with a +31.7pt gap between required and delivered growth. Quality "
    "does not exempt it from cycle math.",
    ["Process-control intensity rises structurally with advanced nodes -- the moat is real"],
    ["41%/yr growth for five years from a 10%/yr franchise"],
    ["WFE guidance updates", "Memory/logic capex mix shifts"],
    ["Equipment digestion cycles hit inspection spend with the same violence",
     "Operating margin unverifiable from XBRL (untagged subtotal)"],
    ["Backlog decline two quarters", "WFE industry guidance cut",
     "FCF margin below 25%"],
    "KLAC at the 100th percentile is the AMAT/LRCX case with a better "
    "moat and a worse price: EV/revenue of 21x for an equipment vendor "
    "exceeds even most software. The insiders selling into the split-"
    "adjusted highs have process-control-grade visibility.",
    ["Base multiple 36x is far above the semicap historical band",
     "EPS-based scenarios rest on split-adjusted per-share figures (factor 10, documented)"],
    ["Operating-income subtotal untagged; margin structure partially unverifiable"],
    ["100th-percentile P/E, 21.3x EV/revenue at cycle peak"],
    ["Process-control vs services mix (pending)"],
    "UNATTRACTIVE", "MEDIUM", 33,
    [C("The shares trade at 59.4x split-consistent TTM earnings, the 100th percentile of five-year history, after a +134.9% year.",
       "FACT", "YAHOO:CHART:KLAC", "SEC:ACCESSION:0000319201-26-000016"),
     C("Price-implied growth is 41.4%/yr vs 9.7% delivered 3-year CAGR.",
       "FACT", "YAHOO:CHART:KLAC"),
     C("Market cap ($275B) is now verified against both share bases after the 10:1 split reconciliation.",
       "FACT", "SM:DATA_QUALITY:KLAC"),
     C("Probability-weighted 12-month value is ~30% below the current price.",
       "FORECAST")])


# ---- post-data-fix revisions (override earlier batches via merge order) ----

SPECS_EXTRA3["MCD"] = S(
    "Franchise royalty machine, re-analyzed after the share-scale data fix: "
    "TTM EPS is now verified at $12.16 (P/E 21.8 — the 10th percentile of "
    "its own five-year history, a fact the corrupt data previously hid). "
    "46.3% operating margins, +9.4% revenue growth, $38.9B net debt. Price "
    "implies 20.0%/yr vs 5.1% delivered.",
    "STABLE", "MODERATE",
    ["Operating margin 46.3%; revenue +9.4% YoY on pricing and digital mix",
     "P/E at the 10th percentile of own history — cheaper than it has looked in years"],
    ["$38.9B net debt services the buyback machine",
     "Traffic softness at low-income cohorts persists"],
    ["Value-menu traffic recovery", "International franchise growth"],
    sc((11.0, 16.0), (12.8, 20.0), (13.8, 24.0), 0.25, 0.55, 0.20),
    "With clean data, MCD is a fair-priced royalty rather than the premium "
    "trap it appeared: 21.8× for a 46%-margin annuity at its own 10-year "
    "valuation lows. The implied-growth premium (+15pt over delivered) "
    "still argues patience over enthusiasm — WATCH with a better floor "
    "than previously assessed.",
    ["Pricing power could outrun traffic softness for years"],
    ["High-teens implied growth vs 5% delivered"],
    ["Quarterly comps and traffic mix", "Value-platform performance"],
    ["Low-income traffic erosion is a demand-quality signal",
     "GLP-1 tail risk on volume"],
    ["Comp traffic negative two quarters", "Franchisee health metrics deteriorating"],
    "A royalty at fair value is still exposed to its first franchisee-"
    "economics crisis in a generation: labor costs and traffic softness "
    "squeeze operators before they squeeze the parent, and $39B of debt "
    "means the buyback stops exactly when the multiple needs defending.",
    ["Base case assumes franchisee health holds through the value-menu margin push"],
    ["EPS normalization repaired this cycle (share-scale fix); prior corrupt value dropped by guard"],
    ["21.8× at the 10th percentile is fair-to-cheap; the implied-growth premium is the residual concern"],
    ["Franchisee-level economics (not disclosed granularly)"],
    "WATCH", "MEDIUM", 52,
    [C("Verified TTM diluted EPS is $12.16 (P/E 21.8, 10th percentile of five-year history) after the share-scale data repair.",
       "FACT", "SEC:ACCESSION:0000063908-26-000051", "SM:DATA_QUALITY:MCD"),
     C("Operating margin is 46.3% with revenue +9.4% YoY; net debt is $38.9B.",
       "FACT", "SEC:ACCESSION:0000063908-26-000051"),
     C("Price-implied growth (20.0%/yr) carries a ~15pt premium over delivered (5.1%).",
       "FACT", "YAHOO:CHART:MCD"),
     C("Probability-weighted 12-month value is ~5% below the current price.",
       "FORECAST")])

SPECS_EXTRA3["CMCSA"] = S(
    "Cable incumbent re-analyzed after the share-count reconciliation fix: "
    "market cap is now verified at $98B (splits from 2017/2021 applied to "
    "the stale cover-page count), which confirms a REAL 20.8% FCF yield "
    "and 4.8× P/FCF — among the cheapest cash streams in the market. "
    "Revenue -1.2%, operating income -13.9%: the core is genuinely "
    "eroding; the price now demonstrably over-discounts it.",
    "DETERIORATING", "MODERATE",
    ["Verified: $20.4B FCF against $98B market cap (20.8% yield)",
     "Theme parks and Peacock narrowing losses"],
    ["Broadband subscriber losses to fiber/FWA — the profit pool is shrinking",
     "Operating income -13.9% YoY"],
    ["Broadband sub trajectory", "Capital-return pace at a 21% FCF yield"],
    sc((2.8, 6.0), (3.1, 8.5), (3.4, 11.0), 0.30, 0.50, 0.20),
    "With the market cap verified, the thesis strengthens from 'cheap with "
    "an asterisk' to 'verifiably one of the market's cheapest cash flows': "
    "a 20.8% FCF yield means the equity returns its price in under five "
    "years of buybacks even while declining. Decay is real; the price "
    "over-discounts it. Upgraded to ATTRACTIVE with decay-tripwire "
    "invalidations.",
    ["At 4.8× FCF, even -5%/yr perpetual decay yields double-digit returns via buybacks"],
    ["Rapid terminal decline far worse than the observed -1.2%"],
    ["Quarterly broadband net losses", "Parks bookings; buyback announcements"],
    ["Fiber overbuild accelerating into CMCSA footprints",
     "Media segment secular pressure"],
    ["Broadband losses above 300k/quarter", "OI decline worse than -15% again",
     "FCF below $16B TTM"],
    "Cable's terminal phase can be fast, and harvest-mode managements "
    "rarely shrink gracefully — the risk is cash getting redeployed into "
    "media adventures instead of buybacks. The verified yield only pays "
    "if capital returns actually happen.",
    ["Assumes capital returns absorb most of the $20B FCF",
     "Share-count reconciliation now verified but was broken until today — monitor next quarter's continuity"],
    ["Share-count fix verified this cycle; both share bases now agree within 4%"],
    ["None at 4.8× FCF — cheapness is no longer in question, only durability"],
    ["Broadband vs media segment split (pending)"],
    "ATTRACTIVE", "MEDIUM", 60,
    [C("Market cap is verified at $98B after split reconciliation; TTM FCF of $20.4B gives a 20.8% yield (4.8× P/FCF).",
       "FACT", "SEC:ACCESSION:0001628280-26-049360", "SM:DATA_QUALITY:CMCSA"),
     C("Revenue declined 1.2% YoY with operating income -13.9% — the core is eroding.",
       "FACT", "SEC:ACCESSION:0001628280-26-049360"),
     C("The price over-discounts the observed decay rate.",
       "INFERENCE", "YAHOO:CHART:CMCSA"),
     C("Probability-weighted 12-month value is ~+15% above the current price.",
       "FORECAST")])


SPECS_EXTRA3["SOFI"] = S(
    "Digital consumer bank, covered at last under bank adapter v1: verified "
    "TTM total net revenue $3.94B (+42.6% YoY, NII $2.41B + noninterest "
    "$1.53B cross-checks exactly), deposits $40.2B growing 47.6%/yr, "
    "equity/assets 20.1%. The CEO bought stock FOUR times with zero sales, "
    "beats run +30%/+32%/+13%, and the price implies 23.3%/yr (equity "
    "net-income basis) -- BELOW both delivered (+31.9% CAGR) and consensus "
    "(+19.2% next FY plus operating leverage). Adapter caveats stated: "
    "loan-flow metrics suppressed; credit book is fair-value accounted and "
    "cycle-untested.",
    "IMPROVING", "STRONG",
    ["Revenue +42.6% YoY with deposits +47.6% -- the funding-cost flywheel is working",
     "CEO made four discretionary open-market purchases, zero sales; beats at +30%, +32%, +13%"],
    ["ROE still just 5.3% -- profitability lags the growth story",
     "Heavy dilution history (capital-allocation score 0)"],
    ["Operating leverage: efficiency ratio (82.8%) grinding down as revenue scales",
     "Credit normalization: provisions at only 0.85% of revenue on a young, fair-value book"],
    sc((0.35, 25.0), (0.60, 30.0), (0.90, 40.0), 0.30, 0.45, 0.25),
    "A hypergrowth bank priced below its own delivered growth rate after a "
    "-22.4% year, with the strongest insider-conviction signal in the "
    "coverage universe (4 CEO buys). The 39x P/E is high on today's "
    "depressed ROE and cheap on any credible operating-leverage path. "
    "ATTRACTIVE with conviction capped LOW: the adapter is v1 and the "
    "loan book has never seen a credit cycle.",
    ["The market prices SOFI as a lender; the deposit franchise + fee mix is compounding toward a bank-as-platform model"],
    ["Growth deceleration below consensus: 23.3%/yr implied vs 31.9% delivered"],
    ["Earnings 2026-07-29 (3 days)", "Deposit growth and efficiency-ratio prints"],
    ["Fair-value loan accounting defers credit pain until it arrives all at once",
     "Student/personal-loan credit normalization in a downturn",
     "Dilution resuming to fund growth"],
    ["Deposit growth below 15%/yr", "Provisions above 3% of revenue",
     "Revenue growth below 20%", "Efficiency ratio rising two consecutive quarters"],
    "SOFI is a personal-loan machine wearing a bank charter: the 0.85% "
    "provision rate on a fair-value book is not conservatism, it is "
    "accounting timing -- and every young lender's first credit cycle "
    "reprices both the book and the multiple simultaneously. At 39x "
    "earnings, the insurance against that scenario is zero.",
    ["Scenario EPS assumes operating leverage persists through any credit normalization",
     "Bank adapter v1: NIM proxy and suppressed metrics are approximations, documented"],
    ["Fair-value loan accounting masks through-cycle loss content; provisions/revenue 0.85% is untested",
     "Bank-mode suppressions applied (cash-flow/accrual metrics are loan flows)"],
    ["39x trailing on 5.3% ROE is expensive today, cheap only if ROE triples on plan"],
    ["Segment economics (lending vs tech platform vs financial services) pending"],
    "ATTRACTIVE", "LOW", 58,
    [C("Verified TTM total net revenue is $3.94B (+42.6% YoY); NII $2.41B + noninterest income $1.53B cross-check exactly.",
       "FACT", "SEC:ACCESSION:0001818874-26-000037", "SM:DATA_QUALITY:SOFI"),
     C("Deposits are $40.2B, up 47.6% YoY; equity/assets is 20.1%; ROE is 5.3%.",
       "FACT", "SEC:ACCESSION:0001818874-26-000037"),
     C("The CEO made four discretionary open-market purchases with zero discretionary sales in the trailing 6 months.",
       "FACT", "SEC:FORM4:SOFI"),
     C("Price-implied growth (23.3%/yr, equity basis) sits below delivered (31.9%) and near consensus (+19.2%).",
       "FACT", "YAHOO:CHART:SOFI"),
     C("Probability-weighted 12-month value is ~+20% above the current price with a wide, credit-cycle-dependent distribution.",
       "FORECAST")])
