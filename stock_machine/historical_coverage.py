"""Read-only inventory. Stored rows alone do not establish causal coverage."""


def inventory(conn):
    queries = {
        'consensus_precise_vintages': 'SELECT count(*),count(DISTINCT ticker),min(observed_at)::text,max(observed_at)::text FROM consensus_vintages',
        'consensus': "SELECT count(*),count(DISTINCT ticker),min(snapshot_date)::text,max(snapshot_date)::text FROM consensus_snapshots WHERE period_type='annual' OR period_basis='fiscal'",
        'earnings_surprise_vintages': 'SELECT count(*),count(DISTINCT ticker),min(observed_at)::text,max(observed_at)::text FROM earnings_surprise_vintages',
        'option_surfaces': 'SELECT count(*),count(DISTINCT ticker),min(as_of)::text,max(as_of)::text FROM option_surface_snapshots',
        'macro_vintages': 'SELECT count(*),count(DISTINCT series_id),min(available_at)::text,max(available_at)::text FROM macro_series_vintages',
    }
    result = {}
    with conn.cursor() as cur:
        for name, query in queries.items():
            cur.execute(query)
            row = cur.fetchone()
            result[name] = dict(zip(('stored_rows', 'entities', 'first_available', 'last_available'), row))
    result['interpretation'] = (
        'Availability dates describe stored vintages, not economic period dates. '
        'Legacy consensus dates have daily resolution; precise vintages retain source and timestamp. Matched panel coverage is reported separately; '
        'current estimates, current option chains and newly fetched earnings surprises do not reconstruct historical knowledge.'
    )
    return result
