# Local Setup

## Environment Variables

Have the following environment variables.

```
TMDB_API_READ_ACCESS_TOKEN
TMDB_API_KEY
```

## Python Environment

Create a local Python environment and install the dependencies.

```bash
python -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`
pip install -r scripts/requirements.txt
pip install -r scripts/dev-requirements.txt
```

## Fetching Data

To fetch data from the TMDB API, run the following script:

```bash
python scripts/fetch_data.py
```

Monitor the console for any errors.
