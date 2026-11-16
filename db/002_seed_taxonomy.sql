-- Starter canonical taxonomy for finance / economics / politics.
-- This is the base the inference LLM consults; it picks ONE canonical path per
-- concept to resolve the fragmentation seen in the raw data
-- (finance/macro/economics/econ; politics/policy/gov/governance; crypto/crypto_markets).
-- Embeddings are left NULL here; backfill them with your chosen embedder, then
-- build the HNSW index, before enabling new-node canonicalization.
--
-- Convention: ltree `path` uses '.' + underscores; `slug` mirrors it with '>' + hyphens.

insert into topic_nodes (path, slug, label) values
  ('finance',                              'finance',                               'finance'),
  ('finance.us_equities',                  'finance>us-equities',                   'us equities'),
  ('finance.us_equities.earnings',         'finance>us-equities>earnings',          'earnings'),
  ('finance.us_equities.single_stock',     'finance>us-equities>single-stock',      'single stock'),
  ('finance.us_equities.semis',            'finance>us-equities>semis',             'semiconductors'),
  ('finance.crypto',                       'finance>crypto',                        'crypto'),
  ('finance.crypto.btc',                   'finance>crypto>btc',                    'bitcoin'),
  ('finance.commodities',                  'finance>commodities',                   'commodities'),
  ('finance.commodities.energy',           'finance>commodities>energy',            'energy'),
  ('finance.commodities.energy.oil',       'finance>commodities>energy>oil',        'oil'),

  ('economics',                            'economics',                             'economics'),
  ('economics.monetary_policy',            'economics>monetary-policy',             'monetary policy'),
  ('economics.monetary_policy.fed_rates',  'economics>monetary-policy>fed-rates',   'fed rates'),
  ('economics.monetary_policy.inflation',  'economics>monetary-policy>inflation',   'inflation'),
  ('economics.macro',                      'economics>macro',                       'macro'),
  ('economics.macro.gdp',                  'economics>macro>gdp',                   'gdp'),
  ('economics.labor',                      'economics>labor',                       'labor'),
  ('economics.labor.unemployment',         'economics>labor>unemployment',          'unemployment'),

  ('politics',                             'politics',                              'politics'),
  ('politics.us',                          'politics>us',                           'us politics'),
  ('politics.us.elections',                'politics>us>elections',                 'elections'),
  ('politics.legislation',                 'politics>legislation',                  'legislation'),
  ('politics.legislation.bill_passage',    'politics>legislation>bill-passage',     'bill passage'),
  ('politics.geopolitics',                 'politics>geopolitics',                  'geopolitics')
on conflict (path) do nothing;
