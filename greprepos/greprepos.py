#!/usr/bin/env python3.10

"""
Iterate over a namespace of GitHub repos and generate
a CSV file of information about each one.
"""

import argparse


from github import Github
from github.GithubException import UnknownObjectException


def _get_github_data(token, org_name):
    """Get repository data from GitHub, for all repositories in org_name."""

    api = Github(login_or_token=token, timeout=60)
    data = {}
    org = api.get_organization(org_name)
    for repo in org.get_repos():
        repo_info = {}
        repo_info["name"] = repo.name
        repo_info["is_archived"] = repo.archived
        repo_info["created_at"] = repo.created_at
        repo_info["pushed_at"] = repo.pushed_at
        repo_info["default_branch"] = repo.default_branch
        repo_info["has_main_branch"] = "main" in [branch.name for branch in repo.get_branches()]
        repo_info["is_private"] = repo.private
        try:
            repo.get_license()
            repo_info["has_license_file"] = True
        except UnknownObjectException:
            repo_info["has_license_file"] = False
        repo_info["topics"] = ",".join(repo.get_topics())
        repo_info["forks_count"] = repo.forks_count
        repo_info["open_issues"] = repo.open_issues_count
        repo_info["open_prs"] = repo.get_pulls("open").totalCount
        data[repo.name] = repo_info
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
