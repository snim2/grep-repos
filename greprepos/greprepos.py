#!/usr/bin/env python3.10

"""
Iterate over a namespace of GitHub repos and generate
a CSV file of information about each one.
"""

from datetime import datetime

import argparse
import calendar
import csv
import time

from github import Github
from github.GithubException import RateLimitExceededException, UnknownObjectException


# Format for parsing GitHub datestamps.
_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# When waiting for the rate limit to be reset, we add _TIME_DELTA seconds to
# the wait time, to tbe sure that the next api call happens after the reset.
_TIME_DELTA = 10


def _get_github_data(token, org_name):
    """Get repository data from GitHub, for all repositories in org_name.

    If we hit the API rate limit, wait until it has been reset, and carry on.
    The default value for timeout is 15 seconds. We use a much larger value, to
    avoid having to restart this method from scratch. In practice, timeout
    errors still occur, and so the value passed to retry ensures that API calls
    are automatically retried after a timeout or a temporary error.
    """

    api = Github(login_or_token=token, timeout=60, retry=3)
    data = {}
    org = api.get_organization(org_name)
    default_contrib = _get_default_contrib(org)
    default_coc = _get_default_coc(org)
    org_repos = org.get_repos()
    for repo in org_repos:
        try:
            data[repo.name] = _get_repo_data(repo, default_contrib, default_coc)
        except RateLimitExceededException:
            rate_limit = api.get_rate_limit().core
            print(f"Rate limit remaining: {rate_limit.remaining}")
            reset_timestamp = calendar.timegm(rate_limit.reset.timetuple())
            sleep_time = reset_timestamp - calendar.timegm(time.gmtime()) + _TIME_DELTA
            time.sleep(sleep_time)
            data[repo.name] = _get_repo_data(repo, default_contrib, default_coc)
            continue
    assert (
        len(data.keys()) == org_repos.totalCount
    ), f"Got {len(data.keys())} repos but expected {org_repos.totalCount}."
    return data


def _get_repo_data(repo, default_contrib, default_coc):
    """Return a dict of repository information for one repo.

    Note that the dictionary keys here need to match those in _write_csv_file().
    """
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
    repo_info["has_contributing"] = _repo_has_file(repo, "CONTRIBUTING.md")
    if repo_info["has_contributing"]:
        repo_info["contrib_matches_org_default"] = repo.get_contents("CONTRIBUTING.md").content == default_contrib
    else:
        repo_info["contrib_matches_org_default"] = False
    repo_info["has_code_of_conduct"] = _repo_has_file(repo, "CODE_OF_CONDUCT.md")
    if repo_info["has_code_of_conduct"]:
        repo_info["coc_matches_org_default"] = repo.get_contents("CODE_OF_CONDUCT.md").content == default_coc
    else:
        repo_info["coc_matches_org_default"] = False
    repo_info["topics"] = ",".join(repo.get_topics())
    repo_info["forks_count"] = repo.forks_count
    repo_info["open_issues"] = repo.open_issues_count
    repo_info["open_prs"] = repo.get_pulls("open").totalCount
    return repo_info


def _get_default_contrib(org):
    """Get the default CONTRIBUTING.md for a given organization."""

    return _get_default_file(org, "CONTRIBUTING.md")


def _get_default_coc(org):
    """Get the default CODE_OF_CONDUCT.md for a given organization."""

    return _get_default_file(org, "CODE_OF_CONDUCT.md")


def _get_default_file(org, filename):
    """Get the default version of filename for a given organization."""

    default_contrib = None
    try:
        repo = org.get_repo(".github")
        default_contrib = repo.get_contents(filename).content
    except UnknownObjectException:
        pass
    return default_contrib


def _repo_has_file(repo, filename):
    """Return True if repo contains filename and False otherwise."""

    try:
        repo.get_contents(filename)
    except UnknownObjectException:
        return False
    return True


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
        "has_contributing",
        "contrib_matches_org_default",
        "has_code_of_conduct",
        "coc_matches_org_default",
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
