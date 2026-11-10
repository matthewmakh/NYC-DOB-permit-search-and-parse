#!/usr/bin/env python3
"""Step 3b: tag recorded mortgages with the lender's investor loan program
(DSCR / fix & flip / bridge). Reads only rows step 3 already stored, so it
needs no network access. The canonical module lives in dashboard_html/ so the
dashboard and the per-building ACRIS refresh can import it too."""
import _pipeline_path  # noqa: F401  (puts dashboard_html on sys.path)
from dotenv import load_dotenv

load_dotenv()
load_dotenv('dashboard_html/.env')

from lender_programs import main  # noqa: E402

if __name__ == '__main__':
    main()
