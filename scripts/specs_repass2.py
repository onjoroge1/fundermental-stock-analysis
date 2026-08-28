"""Re-pass batch (2026-08-28): the 9 reports invalidated by filings that
landed while the pipeline was frozen. Merge order lets these override all
earlier specs.

Three carry breached invalidation conditions (CRM, HPQ, LRCX). A breach
forces a deliberate re-read, not an automatic downgrade — but where the
mechanism a thesis depended on has actually failed, the verdict changes.
"""

from specs_extra import S, C, sc

REPASS2 = {}

REPASS2["NVDA"] = S(
    "Post-FY27-Q2 re-pass and an upgrade: revenue +105.9% YoY with operating "
    "income +124% and $127B TTM free cash flow, while the multiple sits at "
    "28.7x — the 5th percentile of its own five-year history. The price now "
    "implies 27.9%/yr against 100% delivered. For the first time in this "
    "coverage, NVDA carries a negative expectations gap.",
    "IMPROVING", "STRONG",
    ["Revenue +105.9% YoY; operating margin 65.2%; FCF margin 41.9%",
     "P/E 28.7 at the 5th percentile of own history — the cheapest it has been",
     "Beats steady: +5.2%, +6.3%, +6.2%"],
    ["Receivables grew 20.9pt faster than revenue — worth watching at this scale",
     "Insiders sold $448.9M in the window"],
    ["Whether hyperscaler capex digests in 2027",
     "Customer concentration: a handful of buyers fund most of the growth"],
    sc((6.5, 22.0), (9.0, 30.0), (11.5, 38.0), 0.30, 0.45, 0.25),
    "The market has spent a year de-rating NVDA while earnings more than "
    "doubled: the stock is up only 26.7% against 106% revenue growth. At "
    "28.7x with a requirement of 27.9%/yr — a quarter of what it just "
    "delivered — the expectations bar is finally below the business.",
    ["Compression of the multiple, not the business, drove the year; a merely-good 2027 clears the priced bar"],
    ["A sharp deceleration to roughly a quarter of the current growth rate"],
    ["Earnings 2026-11-18", "Hyperscaler capex guidance for 2027"],
    ["A capex digestion year would hit revenue and multiple together",
     "$449M of insider selling is not a vote of confidence",
     "Receivables outpacing revenue by 20.9pt at $303B scale is a large absolute build"],
    ["Revenue growth below 30%", "Receivables gap above 30pt",
     "Gross margin below 70%", "A hyperscaler publicly cutting AI capex"],
    "Cyclical peaks always look cheap on trailing earnings — that is what a "
    "peak IS. If 2027 is the digestion year, today's 28.7x is 60x on "
    "normalised earnings and the 5th-percentile multiple was a warning, not "
    "a bargain. The $449M insider sale is consistent with that reading.",
    ["Scenario EPS assumes no digestion year inside the horizon",
     "5th-percentile P/E is only meaningful if current earnings are sustainable"],
    ["Receivables +20.9pt vs revenue"],
    ["The bull case rests on trailing earnings that are, by construction, cycle-peak"],
    ["Customer concentration detail (not disclosed granularly)"],
    "ATTRACTIVE", "MEDIUM", 66,
    [C("FY2027-Q2 revenue was $96.2B; TTM revenue grew 105.9% YoY with operating income +124.1% and $127.0B FCF.",
       "FACT", "SEC:ACCESSION:0001045810-26-000075"),
     C("The shares trade at 28.7x TTM earnings — the 5th percentile of five-year history — after a +26.7% twelve-month return.",
       "FACT", "YAHOO:CHART:NVDA"),
     C("Price-implied growth (27.9%/yr) is roughly a quarter of delivered growth.",
       "FACT", "YAHOO:CHART:NVDA", "SEC:ACCESSION:0001045810-26-000075"),
     C("Insiders sold $448.9M and receivables grew 20.9 points faster than revenue.",
       "FACT", "SEC:FORM4:NVDA", "SEC:ACCESSION:0001045810-26-000075"),
     C("Probability-weighted 12-month value is above the current price.",
       "FORECAST")])

REPASS2["CRM"] = S(
    "Post-FY27-Q2 re-pass with a BREACHED invalidation and a downgrade: the "
    "thesis required margin expansion, and it stopped — operating margin is "
    "19.88%, through the 20% floor the report itself set, with operating "
    "income flat YoY (-0.04%) on +10.8% revenue. Cash generation remains "
    "excellent ($15.2B FCF, 7.3% yield) and three insiders are still buying, "
    "but the mechanism the ATTRACTIVE call rested on has failed.",
    "MIXED", "WEAK",
    ["FCF $15.2B at a 34.5% margin; 7.3% FCF yield; P/E 21.4 at the 10th percentile",
     "THREE discretionary insider purchases across two owners, zero sales",
     "Price implies just 2.2%/yr against 9.8% delivered"],
    ["INVALIDATION BREACHED: operating margin 19.88% (floor 20%), OI growth -0.04%",
     "SBC 8.3% of revenue continues to dilute per-share economics"],
    ["Whether margin expansion resumes or 2026 was the peak",
     "Agentic-AI monetisation vs seat cannibalisation"],
    sc(150.0, 245.0, 330.0, 0.30, 0.50, 0.20),
    "Downgrading ATTRACTIVE to WATCH on discipline, not on price. The "
    "valuation case is stronger than at the last pass — cheaper multiple, "
    "lower embedded expectations, insiders still buying — but the report "
    "named margin expansion as the thesis and named sub-20% margin as its "
    "invalidation. That condition fired. Re-earning ATTRACTIVE requires a "
    "quarter of margin recovery, not a lower price.",
    ["If the margin dip is AI-investment timing rather than seat compression, the thesis resumes intact"],
    ["Roughly nothing: 2.2%/yr implied against 9.8% delivered"],
    ["Next earnings: margin trajectory is the single number that matters",
     "cRPO growth and agent-product attach"],
    ["Seat-model compression would make flat margins the new normal",
     "$27B net debt limits flexibility if growth slows further"],
    ["Operating margin below 19% next quarter", "Revenue growth below 8%",
     "Insider buying reversing to net selling"],
    "Flat operating income on 10.8% revenue growth is the signature of a "
    "business buying its growth. If agents compress seats while AI spend "
    "rises, 34% FCF margins are harvest-mode economics on a plateauing base "
    "— cheap for a reason.",
    ["Base case assumes margin recovery within two quarters — explicitly unproven",
     "Fair values are FCF-multiple judgments; no consensus coverage on this plan tier"],
    ["Margin invalidation breached; SBC 8.3% of revenue"],
    ["Cheapness is now the whole case, and cheapness alone was a losing factor in our own backtest"],
    ["cRPO/bookings detail (pending transcripts)"],
    "WATCH", "MEDIUM", 55,
    [C("FY2027-Q2 operating margin is 19.88% with TTM operating income growth of -0.04% on +10.8% revenue.",
       "FACT", "SEC:ACCESSION:0001108524-26-000190"),
     C("The om_lt_20 invalidation condition set by the prior report is formally breached.",
       "FACT", "SM:INVALIDATION:CRM"),
     C("TTM FCF is $15.2B (7.3% yield) at 21.4x earnings, the 10th percentile of own history; three insiders bought with zero sales.",
       "FACT", "SEC:ACCESSION:0001108524-26-000190", "SEC:FORM4:CRM"),
     C("The valuation improved while the thesis mechanism failed — discipline downgrades on the mechanism.",
       "INFERENCE", "SM:INVALIDATION:CRM"),
     C("Probability-weighted 12-month value remains above price, but conviction is withdrawn pending margin recovery.",
       "FORECAST")])

REPASS2["META"] = S(
    "Post-FY26-Q2 re-pass and a downgrade: revenue grew 28.0% but operating "
    "income FELL 8.2% YoY as AI capex and headcount hit the P&L, and the "
    "most recent quarter MISSED by 14.1% — the first miss in this coverage "
    "window. The July ATTRACTIVE call rested on operating leverage; that "
    "leverage has inverted.",
    "MIXED", "MODERATE",
    ["Revenue +28.0% YoY — demand is not the problem",
     "P/E 21.5 at the 30th percentile; $41.0B TTM FCF"],
    ["Operating income -8.2% YoY: spending is growing faster than revenue",
     "Latest surprise -14.1%; expectations score fell to 32.4",
     "SBC 11.0% of revenue"],
    ["Whether AI capex converts to revenue or becomes a permanent cost base",
     "Reality Labs losses vs core advertising margin"],
    sc((23.0, 17.0), (27.0, 22.0), (31.0, 27.0), 0.30, 0.50, 0.20),
    "The stock is down 23.7% and the multiple looks reasonable, but the "
    "mechanism has changed: a 28%-growth company whose operating income is "
    "shrinking is spending its way through a transition with no disclosed "
    "return schedule. WATCH until margins stabilise.",
    ["If AI spend is front-loaded and converts, the operating leverage returns violently"],
    ["Roughly in line: 22.6%/yr implied vs 19.9% delivered revenue CAGR"],
    ["Earnings 2026-10-28: the margin line, not the revenue line",
     "Any capex guidance moderation"],
    ["Open-ended AI spend with no disclosed ROI horizon",
     "A second consecutive miss would reset expectations sharply"],
    ["Operating income declining again next quarter", "Revenue growth below 15%",
     "SBC above 13% of revenue"],
    "Every mega-cap that entered an open-ended capex cycle saw margins "
    "compress longer than management guided. A -14% miss alongside -8% "
    "operating income is not a wobble; it is the cost base outrunning the "
    "business, and 21.5x is not cheap enough to ignore that.",
    ["Base case assumes margin stabilisation the last two quarters contradict"],
    ["SBC 11.0% of revenue"],
    ["The multiple is only attractive against earnings that are currently falling"],
    ["Reality Labs vs core segment margins (pending)"],
    "WATCH", "MEDIUM", 54,
    [C("FY2026-Q2 revenue was $60.8B; TTM revenue grew 28.0% while operating income declined 8.2% YoY.",
       "FACT", "SEC:ACCESSION:0001628280-26-050705"),
     C("The most recent earnings surprise was -14.1%; the expectations score fell to 32.4.",
       "FACT", "FMP:EARNINGS:META"),
     C("Operating leverage — the basis of the prior ATTRACTIVE call — has inverted.",
       "INFERENCE", "SEC:ACCESSION:0001628280-26-050705"),
     C("Probability-weighted 12-month value is modestly above price; conviction withdrawn pending margin evidence.",
       "FORECAST")])

REPASS2["HPQ"] = S(
    "Post-FY26-Q3 re-pass with a BREACHED invalidation: receivables grew "
    "27.4pt faster than revenue, through the 20pt line the prior report set "
    "as its falsifier. The operating numbers improved (revenue +12.5%, "
    "operating income +24.6%, a 14.5% FCF yield at 11.2x earnings) — which "
    "is exactly what makes the working-capital signal worth taking "
    "seriously rather than explaining away.",
    "MIXED", "MODERATE",
    ["Revenue +12.5% YoY with operating income +24.6% — the AI-PC refresh is real",
     "FCF yield 14.5% at 11.2x earnings; buybacks retire shares steadily"],
    ["INVALIDATION BREACHED: receivables +27.4pt vs revenue (threshold 20pt)",
     "Print supplies decline remains structural"],
    ["Whether the receivables build converts to cash or reverses",
     "AI-PC refresh durability past the initial upgrade wave"],
    sc((2.4, 8.0), (2.9, 11.0), (3.4, 14.0), 0.35, 0.45, 0.20),
    "Cheap and improving, with one flag that undercuts both: revenue growing "
    "12.5% while receivables grow 40% is the arithmetic of a channel being "
    "loaded. Until that gap narrows, the earnings and the FCF yield are "
    "provisional. WATCH with conviction cut, not a value call.",
    ["If the build is genuine AI-PC demand financed on normal terms, the multiple is far too low"],
    ["Accelerating decline (-12%/yr implied) that the operating numbers contradict"],
    ["Next earnings: receivables and channel-inventory disclosure",
     "PC unit data from the broader industry"],
    ["Channel loading pulls forward revenue that must be given back",
     "Print profit pool declines regardless of the PC cycle"],
    ["Receivables gap above 20pt again next quarter", "FCF below $3B TTM",
     "Gross margin below 18%"],
    "A 27.4pt receivables-revenue divergence is the same signature that made "
    "DELL an UNATTRACTIVE call: revenue shipped on terms rather than sold "
    "for cash. In a low-margin hardware business, that gap is the difference "
    "between a 14.5% FCF yield and a write-down.",
    ["Scenario EPS treats the receivables build as benign — the breach argues otherwise"],
    ["Receivables invalidation breached (+27.4pt)"],
    ["A 14.5% FCF yield is only real if the receivables collect"],
    ["Channel inventory levels (not disclosed)"],
    "WATCH", "LOW", 47,
    [C("FY2026-Q3 revenue grew 12.5% TTM YoY with operating income +24.6%; FCF yield is 14.5% at 11.2x earnings.",
       "FACT", "SEC:ACCESSION:0000047217-26-000051"),
     C("Receivables grew 27.4 points faster than revenue, breaching the 20pt invalidation condition.",
       "FACT", "SEC:ACCESSION:0000047217-26-000051", "SM:INVALIDATION:HPQ"),
     C("Improving operating results alongside a widening receivables gap is the channel-loading signature.",
       "INFERENCE", "SEC:ACCESSION:0000047217-26-000051"),
     C("Probability-weighted value is above price, but conviction is LOW until the gap narrows.",
       "FORECAST")])

REPASS2["LRCX"] = S(
    "Post-FY26-Q4 re-pass, verdict unchanged and now with a quality flag: "
    "revenue +30.0% and operating income +44.4% confirm the memory-equipment "
    "cycle, but the price requires 46.0%/yr against a 10.1% delivered CAGR "
    "(+44.5pt gap) at 55.1x earnings, the 90th percentile of its own "
    "history — and receivables grew 28.1pt faster than revenue.",
    "IMPROVING", "MODERATE",
    ["Revenue +30.0% YoY, operating income +44.4%, ROIC 57.8%",
     "Memory/HBM capex cycle is genuinely running"],
    ["Receivables +28.1pt vs revenue (generic quality tripwire breached)",
     "P/E 55.1 at the 90th percentile; implied growth 4.5x delivered"],
    ["Memory-maker capex follow-through into 2027", "China export-control exposure"],
    sc((5.0, 26.0), (6.5, 32.0), (8.0, 40.0), 0.35, 0.45, 0.20),
    "The cycle is real and the price already assumes it never ends. A "
    "+208% year has taken the multiple to the top decile of its history on "
    "peak-cycle orders, and the receivables build suggests some of those "
    "orders are being financed rather than paid for.",
    ["If HBM capacity racing extends the cycle through 2028, peak earnings persist longer than history suggests"],
    ["~46%/yr growth — over four times the delivered rate"],
    ["Memory-maker capex guidance", "WFE industry updates"],
    ["Memory capex is the most violent equipment cycle in technology",
     "Receivables divergence at a cycle peak is the classic late signal"],
    ["Backlog declining two quarters", "Receivables gap above 30pt",
     "Memory WFE guidance cut"],
    "Buying the best equipment franchise at its highest-ever multiple, on "
    "orders that are increasingly on terms, at the top of the most cyclical "
    "capex cycle in technology, is a bet that this time the cycle does not "
    "turn.",
    ["Base multiple of 32x exceeds any level this name has sustained"],
    ["Receivables +28.1pt vs revenue"],
    ["90th-percentile P/E on peak-cycle earnings"],
    ["Etch vs deposition mix and China share (pending)"],
    "UNATTRACTIVE", "MEDIUM", 34,
    [C("FY2026-Q4 revenue grew 30.0% TTM YoY with operating income +44.4%.",
       "FACT", "SEC:ACCESSION:0000707549-26-000037"),
     C("The shares trade at 55.1x earnings (90th percentile) with price-implied growth of 46.0%/yr vs 10.1% delivered.",
       "FACT", "YAHOO:CHART:LRCX"),
     C("Receivables grew 28.1 points faster than revenue, breaching the generic quality tripwire.",
       "FACT", "SEC:ACCESSION:0000707549-26-000037", "SM:INVALIDATION:LRCX"),
     C("Probability-weighted 12-month value is well below the current price.",
       "FORECAST")])

REPASS2["AMAT"] = S(
    "Post-FY26-Q3 re-pass, verdict unchanged: revenue +24.8% and operating "
    "income +37.7% are strong, but after a +193% year the multiple is 41.6x "
    "at the 95th percentile of its own history and the price requires "
    "40.5%/yr against 3.2% delivered — a 33.2pt gap. Insiders sold $173.2M.",
    "IMPROVING", "MODERATE",
    ["Revenue +24.8% YoY with operating income +37.7%; ROIC 30.8%",
     "AI-fab buildout demand is genuine"],
    ["3-year delivered CAGR is just 3.2% — this is a cycle, not a trend",
     "95th-percentile valuation; $173.2M of insider selling"],
    ["WFE spending into 2027", "China export-control exposure"],
    sc((9.5, 22.0), (12.0, 28.0), (14.5, 34.0), 0.35, 0.45, 0.20),
    "Semicap at the 95th percentile of its own valuation, requiring twelve "
    "times its delivered growth rate, with insiders selling into the "
    "re-rating. The equipment cycle is real; the price has borrowed several "
    "years of it.",
    ["A multi-year sovereign-fab buildout could stretch the cycle beyond precedent"],
    ["~40%/yr growth — twelve times the delivered rate"],
    ["WFE guidance updates", "China policy changes"],
    ["Equipment digestion cycles routinely cut revenue 30%",
     "Export controls could remove a double-digit revenue slice"],
    ["Backlog declining two quarters", "WFE industry guidance cut",
     "China revenue restrictions widening"],
    "Equipment stocks at 95th-percentile valuations into a capacity "
    "digestion have exactly one historical outcome, and the $173M of insider "
    "selling says the people with fab-utilisation visibility are reducing "
    "exposure.",
    ["Base multiple of 28x sits above the historical semicap norm"],
    ["None material"],
    ["95th-percentile P/E on cycle-peak earnings"],
    ["Logic vs memory equipment mix (pending)"],
    "UNATTRACTIVE", "MEDIUM", 35,
    [C("FY2026-Q3 revenue grew 24.8% TTM YoY with operating income +37.7%.",
       "FACT", "SEC:ACCESSION:0001628280-26-058235"),
     C("The P/E is 41.6x at the 95th percentile of five-year history after a +193.5% twelve-month return.",
       "FACT", "YAHOO:CHART:AMAT"),
     C("Price-implied growth is 40.5%/yr vs a 3.2% delivered 3-year CAGR; insiders sold $173.2M.",
       "FACT", "YAHOO:CHART:AMAT", "SEC:FORM4:AMAT"),
     C("Probability-weighted 12-month value is below the current price.",
       "FORECAST")])

REPASS2["ADI"] = S(
    "Post-FY26-Q3 re-pass, verdict unchanged: the analog recovery is "
    "delivering hard — revenue +39.6% YoY, operating income +97.2% — but "
    "against a -2.8% three-year CAGR, and the price requires 24.4%/yr at "
    "44.3x earnings. Receivables grew 14.2pt faster than revenue; insiders "
    "sold $32.1M.",
    "IMPROVING", "MODERATE",
    ["Revenue +39.6% YoY with operating income +97.2% — deep-cycle recovery leverage",
     "65.8% gross margin held through the downcycle"],
    ["Three-year delivered CAGR remains NEGATIVE (-2.8%)",
     "Receivables +14.2pt vs revenue; ROIC still 10.8% under acquisition goodwill"],
    ["Industrial/auto restocking depth", "Whether recovery run-rate becomes trend"],
    sc((7.5, 26.0), (9.5, 32.0), (11.5, 38.0), 0.30, 0.50, 0.20),
    "A textbook analog snapback priced as a new normal: 44.3x earnings and a "
    "24.4%/yr requirement for a franchise that shrank through the prior "
    "cycle. The recovery is real and largely spent in the multiple.",
    ["Industrial automation demand could genuinely flatten the analog cycle"],
    ["24.4%/yr implied against a negative delivered CAGR"],
    ["Bookings commentary next print", "Auto/industrial channel inventory"],
    ["Restocking ends; run-rate extrapolation is the classic analog trap",
     "Receivables build worth monitoring toward the 15pt tripwire"],
    ["Bookings rollover", "Receivables gap above 15pt", "Gross margin below 60%"],
    "Recovery-year earnings at 44x for a business whose three-year growth is "
    "negative prices a structural change no filing evidences. Insider selling "
    "into the move does not dissent.",
    ["Base multiple of 32x versus an analog historical band nearer 25x"],
    ["Receivables +14.2pt vs revenue — approaching the 15pt tripwire"],
    ["44.3x on trough-recovery earnings"],
    ["End-market mix (pending)"],
    "UNATTRACTIVE", "MEDIUM", 38,
    [C("FY2026-Q3 revenue grew 39.6% TTM YoY with operating income +97.2%; the 3-year CAGR remains -2.8%.",
       "FACT", "SEC:ACCESSION:0000006281-26-000073"),
     C("The shares trade at 44.3x earnings with price-implied growth of 24.4%/yr.",
       "FACT", "YAHOO:CHART:ADI"),
     C("Receivables grew 14.2 points faster than revenue and insiders sold $32.1M.",
       "FACT", "SEC:ACCESSION:0000006281-26-000073", "SEC:FORM4:ADI"),
     C("Probability-weighted 12-month value is below the current price.",
       "FORECAST")])

REPASS2["HD"] = S(
    "Post-FY26-Q2 re-pass, verdict unchanged with a modest improvement: "
    "operating income returned to growth (+4.3% vs -3.0% last pass) on "
    "+5.7% revenue, and the stock fell 17.2% over the year, trimming the "
    "expectations gap to +7.0pt (10.2%/yr implied vs 1.5% delivered). "
    "Receivables +12.8pt vs revenue is below the 15pt tripwire but rising.",
    "STABLE", "MODERATE",
    ["Operating income growth turned positive (+4.3%); FCF yield 4.6%",
     "Pro-segment share gains continue"],
    ["Housing turnover still frozen; 1.5% delivered 3-year CAGR",
     "Receivables +12.8pt vs revenue (pro-credit expansion)"],
    ["Rate path and housing turnover", "Pro vs DIY mix"],
    sc((13.0, 18.0), (15.0, 22.0), (16.5, 26.0), 0.30, 0.50, 0.20),
    "Still a housing-recovery option, now cheaper and no longer shrinking at "
    "the operating line. The +7pt expectations gap must close through rates "
    "falling or price falling; the 4.6% FCF yield pays modestly for waiting.",
    ["A genuine rate-cut cycle validates the premium quickly"],
    ["A housing-turnover recovery inside the forecast horizon"],
    ["Monthly housing turnover data", "Fed rate path"],
    ["Higher-for-longer keeps comps at zero",
     "Pro-credit receivables growth is a cycle-risk marker"],
    ["Comps negative two consecutive quarters", "Receivables gap above 15pt"],
    "Without housing turnover HD is a zero-growth retailer at 23x; the "
    "market has paid for the recovery every year for three years and been "
    "early each time.",
    ["Base case assumes recovery within 12 months",
     "ROIC figure is inflated by lease accounting in our normalisation"],
    ["Receivables +12.8pt vs revenue"],
    ["23x for low-single-digit growth pending a macro turn"],
    ["Pro-credit program exposure (not parsed)"],
    "WATCH", "MEDIUM", 50,
    [C("FY2026-Q2 revenue grew 5.7% TTM YoY with operating income +4.3%, reversing the prior decline.",
       "FACT", "SEC:ACCESSION:0001628280-26-058715"),
     C("The stock fell 17.2% over twelve months; implied growth is 10.2%/yr vs 1.5% delivered.",
       "FACT", "YAHOO:CHART:HD"),
     C("The setup remains a housing-recovery option with a modest carry.",
       "INFERENCE", "SEC:ACCESSION:0001628280-26-058715"),
     C("Probability-weighted 12-month value approximates the current price.",
       "FORECAST")])

REPASS2["LOW"] = S(
    "Post-FY26-Q2 re-pass, verdict unchanged and the pair-trade case "
    "strengthened: revenue +8.3% with operating income +2.3%, the stock down "
    "18.1%, and the price implying just 3.0%/yr. At 17.4x earnings and a "
    "6.0% FCF yield it remains the cheaper way to own the same housing "
    "exposure as HD.",
    "STABLE", "MODERATE",
    ["Revenue +8.3% YoY; FCF yield 6.0% at 17.4x earnings (25th percentile)",
     "Price-implied growth of 3.0%/yr is close to zero expectations"],
    ["Three-year CAGR still negative (-3.9%)",
     "Same frozen-housing exposure as HD, with more DIY cyclicality"],
    ["Housing turnover", "Pro-segment integration progress"],
    sc((11.0, 14.0), (12.5, 17.5), (14.0, 21.0), 0.30, 0.50, 0.20),
    "The cheaper half of the duopoly at near-zero embedded expectations: "
    "6.0% FCF yield, 25th-percentile multiple, and a 3%/yr bar. Absolute "
    "upside still requires housing to thaw, which is why this stays WATCH "
    "rather than ATTRACTIVE.",
    ["Acquisition integration could add share gains independent of the macro"],
    ["Approximately nothing: 3.0%/yr implied"],
    ["Housing turnover data", "Integration milestones"],
    ["DIY exposure is more cyclical than HD's pro mix",
     "Leverage is higher than HD's"],
    ["Comps negative two quarters", "Operating income declining again"],
    "Lowe's discount to HD is structural — weaker locations, weaker pro "
    "franchise — not a mispricing, and a business with a negative three-year "
    "CAGR is still shrinking at any multiple.",
    ["Assumes the +8.3% print is trend change rather than acquisition arithmetic"],
    ["None material"],
    ["25th-percentile P/E is only meaningful if the shrinkage has ended"],
    ["Organic vs acquired growth split (pending)"],
    "WATCH", "MEDIUM", 57,
    [C("FY2026-Q2 revenue grew 8.3% TTM YoY with operating income +2.3%.",
       "FACT", "SEC:ACCESSION:0000060667-26-000117"),
     C("The shares trade at 17.4x earnings with a 6.0% FCF yield; price-implied growth is 3.0%/yr.",
       "FACT", "YAHOO:CHART:LOW"),
     C("The risk/reward remains superior to HD's on identical macro exposure.",
       "INFERENCE", "YAHOO:CHART:LOW", "YAHOO:CHART:HD"),
     C("Probability-weighted 12-month value is modestly above the current price.",
       "FORECAST")])
