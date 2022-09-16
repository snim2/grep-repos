#!/usr/bin/env python3.10

"""
Iterate over a namespace of GitHub repos and generate
a CSV file of information about each one.
"""

import argparse


from github import Github
from github.GithubException import UnknownObjectException


def _get_github_data(token, org_name):
    """Get data from GitHub."""

    api = Github(login_or_token=token, timeout=60)
    data = {}
    org = api.get_organization(org_name)
    for repo in org.get_repos():
        data[repo.name] = []
    return data


def _create_parser():
    """Create a parser for command line arguments."""

    apikey_help = "GitHub personal access token: https://github.com/settings/tokens"
    org_help = "GitHub organization name"

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-a", "--apikey", action="store", type=str, default="", help=apikey_help)
    parser.add_argument("-o", "--organization", action="store", type=str, default="", help=org_help)
    return parser


if __name__ == "__main__":
    options = _create_parser().parse_args()
    repo_data = _get_github_data(options.apikey, options.organization)
    import pprint

    printer = pprint.PrettyPrinter(depth=4)
    printer.pprint(repo_data)
