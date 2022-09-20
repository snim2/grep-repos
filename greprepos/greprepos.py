#!/usr/bin/env python3.10

"""
Iterate over a namespace of GitHub repos and generate
a CSV file of information about each one.
"""

from datetime import datetime

import argparse
import csv

from github import Github
from github.GithubException import UnknownObjectException


_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _get_github_data(token, org_name):
    """Get repository data from GitHub, for all repositories in org_name."""

    api = Github(login_or_token=token, timeout=60)
    data = {}
    org = api.get_organization(org_name)
    for repo in org.get_repos():
        repo_info = {}
        repo_info["name"] = repo.name
        repo_info["is_archived"] = repo.archived
        repo_info["is_private"] = repo.private
        repo_info["is_fork"] = repo.fork
        repo_info["created_at"] = repo.created_at
        repo_info["pushed_at"] = repo.pushed_at
        repo_info["default_branch"] = repo.default_branch
        default_branch = repo.get_branch(repo.default_branch)
        repo_info["commits_on_default_branch"] = repo.get_commits(sha=default_branch.name).totalCount
        last_commit = default_branch.raw_data["commit"]["commit"]["committer"]["date"]
        repo_info["last_commit_to_default_branch"] = datetime.strptime(last_commit, _DATETIME_FORMAT)
        branches = repo.get_branches()
        has_master_branch = "master" in [branch.name for branch in branches]
        has_main_branch = "main" in [branch.name for branch in branches]
        repo_info["has_master_branch_but_no_main"] = has_master_branch and not has_main_branch
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


def _write_csv_file(github_data, csvfile):
    """Write out a CSV file containing the repo_data dictionary."""

    headers = [
        "name",
        "is_archived",
        "is_private",
        "is_fork",
        "created_at",
        "pushed_at",
        "default_branch",
        "commits_on_default_branch",
        "last_commit_to_default_branch",
        "open_issues",
        "open_prs",
        "has_master_branch_but_no_main",
        "has_license_file",
        "topics",
        "forks_count",
    ]
    with open(csvfile, "wt", encoding="Utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(headers)
        for repo in github_data.values():
            row = [repo[column] for column in headers]
            writer.writerow(row)


def _create_parser():
    """Create a parser for command line arguments."""

    apikey_help = "GitHub personal access token: https://github.com/settings/tokens"
    csvfile_help = "Name of the CSV file to be written out, defaults to: github_repo_data.csv"
    org_help = "GitHub organization name"

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-a", "--apikey", action="store", type=str, default="", help=apikey_help)
    parser.add_argument(
        "-c",
        "--csvfile",
        action="store",
        type=str,
        default="github_repo_data.csv",
        help=csvfile_help,
    )
    parser.add_argument("-o", "--organization", action="store", type=str, default="", help=org_help)
    return parser


if __name__ == "__main__":
    options = _create_parser().parse_args()
    repo_data = _get_github_data(options.apikey, options.organization)
    _write_csv_file(repo_data, options.csvfile)
