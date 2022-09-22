#!/usr/bin/env python3.10

"""
Iterate over a namespace of GitHub repos and generate
a CSV file of information about each one.
"""

from argparse import ArgumentParser
from datetime import datetime
from enum import Enum
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


# URL of GitHub instance.
_BASE_URL = "https://github.com"
# Expected filename for codes of conduct.
_CODE_OF_CONDUCT = "CODE_OF_CONDUCT.md"
# Expected filename for contributing instructions.
_CONTRIBUTING = "CONTRIBUTING.md"
# Format for parsing GitHub datestamps.
_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# Name of the repository that holds default files for the organisation.
_ORG_DEFAULT_REPO = ".github"
# When waiting for the rate limit to be reset, we add _TIME_DELTA seconds to
# the wait time, to tbe sure that the next api call happens after the reset.
_TIME_DELTA = 10
# Expected file which describes why a repository is private.
_WHY_PRIVATE = "WHY_PRIVATE.md"
# Type of data gathered from a single GitHub repository. Maps repo_name -> data.
RepoDataType = dict[str, Union[bool, datetime, int, str]]
# Type of data gathered from an entire GitHub organisation. Maps org_name -> data.
OrgDataType = dict[str, RepoDataType]


class RelationshipToOrgDefault(Enum):
    """This enum describes how a file in a repo relates to the default file for the organisation.

    For example, a default CODE_OF_CONDUCT file might be replicated throughout
    the repositories in an organisation.
    """

    LINKS_TO = "links to"  # Repository file links to organisation default.
    MATCHES = "matches"  # File is the same (str equals) as the organisation default.
    MISSING = "missing"  # File is missing in repository.
    NO_DEFAULT = "no organisation default"  # Organisation does not have a default file.
    UNRELATED = "unrelated"  # Files exist and differ.


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
    default_repo = _get_default_repo(org)
    default_contrib = _get_file_contents(default_repo, _CONTRIBUTING) if default_repo is not None else None
    default_coc = _get_file_contents(default_repo, _CODE_OF_CONDUCT) if default_repo is not None else None
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

    repo_info["contributing_relates_to_default"] = _get_relationship_to_org_default(
        default_contrib, _CONTRIBUTING, repo
    ).value
    repo_info["coc_relates_to_default"] = _get_relationship_to_org_default(default_coc, _CODE_OF_CONDUCT, repo).value
    is_private_but_has_no_why_private = False
    if repo_info["is_private"]:
        is_private_but_has_no_why_private = _get_file_contents(repo, _WHY_PRIVATE) is None
    repo_info["missing_why_private"] = is_private_but_has_no_why_private
    repo_info["topics"] = ",".join(repo.get_topics())
    repo_info["forks_count"] = repo.forks_count
    repo_info["open_issues"] = repo.open_issues_count
    repo_info["open_prs"] = repo.get_pulls("open").totalCount
    return repo_info


def _get_relationship_to_org_default(
    default: Optional[str],
    filename: str,
    repo: Repository,
) -> RelationshipToOrgDefault:
    """Does filename in repo match, or link to, the organisation default?"""

    org_name = repo.organization.name
    assert isinstance(org_name, str), f"{repo.name} does not have a related organisation."
    repo_file = _get_file_contents(repo, filename)
    relationship = RelationshipToOrgDefault.MISSING
    if default is None:
        relationship = RelationshipToOrgDefault.NO_DEFAULT
    elif repo_file is None:
        relationship = RelationshipToOrgDefault.MISSING
    elif default == repo_file:
        relationship = RelationshipToOrgDefault.MATCHES
    elif "/".join((_BASE_URL, org_name, _ORG_DEFAULT_REPO, "blob", "main", filename)) in repo_file:
        relationship = RelationshipToOrgDefault.LINKS_TO
    else:
        relationship = RelationshipToOrgDefault.UNRELATED
    return relationship


def _get_default_repo(org: Organization) -> Optional[Repository]:
    """Get the default version of filename for a given organization."""

    default_repo = None
    try:
        default_repo = org.get_repo(_ORG_DEFAULT_REPO)
    except UnknownObjectException:
        pass
    return default_repo


def _get_file_contents(repo: Repository, filename: str) -> Optional[str]:
    """Returns a file from the given repository, or None if the file does not exist.

    This function expects that filename represents a file, and not a directory,
    and will throw an AssertionError if given a directory.
    """

    contents = None
    try:
        content_file = repo.get_contents(filename)
        assert isinstance(content_file, ContentFile), "{filename} does not seem to be a file. Is it a directory?"
        contents = content_file.decoded_content.decode()
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
        "contributing_relates_to_default",
        "coc_relates_to_default",
        "missing_why_private",
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
