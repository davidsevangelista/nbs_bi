-include .env
export

CSV_DIR := data/nbs_corp_card

.PHONY: ingest-ads

## Ingest latest Rain CSV into meta_ads_spend (reads ADS_DATABASE_URL from .env)
ingest-ads:
	@latest=$$(ls -1 $(CSV_DIR)/rain-transactions-export-*.csv 2>/dev/null | sort | tail -1); \
	if [ -z "$$latest" ]; then \
		echo "No Rain CSV found in $(CSV_DIR)/"; exit 1; \
	fi; \
	echo "Ingesting $$latest ..."; \
	nbs-ads-upload "$$latest"
