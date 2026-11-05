# Reddit → NFL Market Prediction (CS 4650 NLP Final Project)

**Team:** Aditi Koratpallikar, Aalind Tyagi, Siddharth Lohani

## Dataset
- **215 games** from the 2025–26 NFL regular season
- **613,813 Reddit comments** from r/nfl game threads
- **76,866 30-second windows** of comment activity
- Kalshi win-probability price history for each game

## Tech Stack
- Python
- pandas
- Reddit JSON API
- Kalshi market data
- scikit-learn
- Hugging Face Transformers (RoBERTa)
- Jupyter / Google Colab

## Results (test set)
| Model | Accuracy | Macro F1 |
|---|---|---|
| TF-IDF + Logistic Regression baseline | 0.473 | 0.445 |
| Transformer (MiniLM + Twitter-RoBERTa embeddings, gated fusion) | **0.544** | **0.518** |
| Zero-shot LLM (Claude Sonnet 4.5, 100-sample subset) | 0.590 | 0.483 |

The Transformer gave the best macro F1. The LLM's higher accuracy mostly comes from predicting FLAT, which makes up 47% of labels.

## My Contributions
- Built the Reddit data collection pipeline (`nfl_reddit_scraper.py`):
  - Reads game-thread links and kickoff times from the season spreadsheet, including games split across multiple threads
  - Recursively parses nested comment trees and expands collapsed "load more" comments through Reddit's `morechildren` endpoint
  - Handles rate limiting with request delays, batch runs, a stop after repeated HTTP 429 errors, and a `--resume` checkpoint that skips games already scraped
  - Buckets comments into 30-second windows anchored to kickoff, from 30 minutes before kickoff up to 4 hours of game time, producing the per-game files in `data_in_buckets/`
- Collected the full 613K-comment dataset that the team's preprocessing and models were built on
