#!/usr/bin/env python3
"""Install sponsored team accounts and payer attribution columns."""

from team_service import init_team_tables


if __name__ == '__main__':
    print('Installing sponsored team-account schema...')
    init_team_tables()
    print('Sponsored team-account schema is ready.')
