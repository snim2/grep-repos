#!/usr/bin/env python3.10

"""
Iterate over a namespace of GitHub repos and generate
a CSV file of information about each one.
"""

from argparse import ArgumentParser
from datetime import datetime
from typing import Optional, Union

import calendar
import csv
import logging
import time

from github import Github
from github.ContentFile import ContentFile
from github.Organization import Organization
from github.Repository import Repository
from github.GithubException import RateLimitExceededException, UnknownObjectException


# Expected filename for codes of conduct.
_CODE_OF_CONDUCT = "CODE_OF_CONDUCT.md"
# Expected filename for contributing instructions.
_CONTRIBUTING = "CONTRIBUTING.md"
# Format for parsing GitHub datestamps.
_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# When waiting for the rate limit to be reset, we add _TIME_DELTA seconds to
# the wait time, to tbe sure that the next api call happens after the reset.
_TIME_DELTA = 10
# Type of data gathered from a single GitHub repository. Maps repo_name -> data.
RepoDataType = dict[str, Union[bool, datetime, int, str]]
# Type of data gathered from an entire GitHub organisation. Maps org_name -> data.
OrgDataType = dict[str, RepoDataType]


def _get_github_data(token: str, org_name: str) -> OrgDataType:
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
    num_repos = org_repos.totalCount
    for index, repo in enumerate(org_repos):
        logging.info("Looking at %s, repo %d of %d.", repo.name, index + 1, num_repos)
        try:
            data[repo.name] = _get_repo_data(repo, default_contrib, default_coc)
        except RateLimitExceededException:
            rate_limit = api.get_rate_limit().core
            logging.info("Rate limit remaining: %d.", rate_limit.remaining)
            reset_timestamp = calendar.timegm(rate_limit.reset.timetuple())
            sleep_time = reset_timestamp - calendar.timegm(time.gmtime()) + _TIME_DELTA
            logging.info("Waiting %ds until rate limit resets.", sleep_time)
            time.sleep(sleep_time)
            data[repo.name] = _get_repo_data(repo, default_contrib, default_coc)
            continue
    assert len(data.keys()) == num_repos, f"Got {len(data.keys())} repos but expected {num_repos}."
    return data


def _get_repo_data(repo: Repository, default_contrib: Optional[str], default_coc: Optional[str]) -> RepoDataType:
    """Return a dict of repository information for one repo.

    Note that the dictionary keys here need to match those in _write_csv_file().
    """
    repo_info: RepoDataType = {}
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

    contributing = _get_file_contents(repo, _CONTRIBUTING)
    repo_info["has_contributing"] = contributing is not None
    if repo_info["has_contributing"] and default_contrib is not None:
        repo_info["contrib_matches_org_default"] = contributing == default_contrib
    else:
        repo_info["contrib_matches_org_default"] = False
    code_of_conduct = _get_file_contents(repo, _CODE_OF_CONDUCT)
    repo_info["has_code_of_conduct"] = code_of_conduct is not None
    if repo_info["has_code_of_conduct"] and default_coc is not None:
        repo_info["coc_matches_org_default"] = code_of_conduct == default_coc
    else:
        repo_info["coc_matches_org_default"] = False
    repo_info["topics"] = ",".join(repo.get_topics())
    repo_info["forks_count"] = repo.forks_count
    repo_info["open_issues"] = repo.open_issues_count
    repo_info["open_prs"] = repo.get_pulls("open").totalCount
    return repo_info


def _get_default_contrib(org: Organization) -> Optional[str]:
    """Get the default contributing instructions for a given organization."""

    return _get_default_file(org, _CONTRIBUTING)


def _get_default_coc(org: Organization) -> Optional[str]:
    """Get the default code of conduct for a given organization."""

    return _get_default_file(org, _CODE_OF_CONDUCT)


def _get_default_file(org: Organization, filename: str) -> Optional[str]:
    """Get the default version of filename for a given organization."""

    default_file = None
    try:
        repo = org.get_repo(".github")
        default_file = _get_file_contents(repo, filename)
    except UnknownObjectException:
        pass
    return default_file


def _get_file_contents(repo: Repository, filename: str) -> Optional[str]:
    """Returns a file from the given repository, or None if the file does not exist.

    This function expects that filename represents a file, and not a directory,
    and will throw an AssertionError if given a directory.
    """

    contents = None
    try:
        content_file = repo.get_contents(filename)
        assert isinstance(content_file, ContentFile), "{filename} does not seem to be a file. Is it a directory?"
        contents = content_file.content
    except UnknownObjectException:
        pass
    return contents


def _write_csv_file(github_data: OrgDataType, csvfile: str) -> None:
    """Write out a CSV file containing the repo_data dictionary."""

    headers: list[str] = [
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


def _create_parser() -> ArgumentParser:
    """Create a parser for command line arguments."""

    apikey_help = "GitHub personal access token: https://github.com/settings/tokens"
    csvfile_help = "Name of the CSV file to be written out, defaults to: github_repo_data.csv"
    org_help = "GitHub organization name"

    parser = ArgumentParser(description=__doc__)
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
    logging.basicConfig(encoding="utf-8", level=logging.INFO)
    options = _create_parser().parse_args()
    repo_data = _get_github_data(options.apikey, options.organization)
    _write_csv_file(repo_data, options.csvfile)
