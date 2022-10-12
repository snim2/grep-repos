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
# Default level for logging. Set at the module level and via a CLI option.
_DEFAULT_LOGLEVEL = "INFO"
# Default filename for generated CSV data.
_DEFAULT_OUTPUT_FILENAME = "github_repo_data.csv"
# Format for parsing GitHub datestamps.
_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# Name of the repository that holds default files for the organisation.
_ORG_DEFAULT_REPO = ".github"
# Title of the Renovate bot configuration PRs.
_RENOVATE_CONFIGURE_PR = "Configure Renovate"
# Username of the bot that generates Renovate PRs.
_RENOVATE_USER = "renovate[bot]"
# When waiting for the rate limit to be reset, we add _TIME_DELTA seconds to
# the wait time, to tbe sure that the next api call happens after the reset.
_TIME_DELTA = 10
# Expected Travis CI config file. Used to detect which repositories need to be
# migrated to GitHub actions.
_TRAVIS_CI_CONFIG = ".travis.yml"
# Expected file which describes why a repository is private.
_WHY_PRIVATE = "WHY_PRIVATE.md"

# Type of data gathered from a single GitHub repository. Maps repo_name -> data.
RepoDataType = dict[str, Union[bool, datetime, int, str]]
# Type of data gathered from an entire GitHub organisation. Maps org_name -> data.
OrgDataType = dict[str, RepoDataType]


# pylint: disable=too-many-locals
# pylint: disable=too-many-statements


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


def _get_github_data(token: str, org_name: str, bot_user: Optional[str]) -> OrgDataType:
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
    num_repos_in_org = org_repos.totalCount
    num_archived = 0
    for index, repo in enumerate(org_repos):
        if repo.archived:
            logging.info("%s is archived. Skipping.", repo.name)
            num_archived += 1
            continue
        logging.info("Looking at %s, repo %d of %d.", repo.name, index + 1, num_repos_in_org)
        try:
            data[repo.name] = _get_repo_data(repo, default_contrib, default_coc, bot_user)
        except RateLimitExceededException:
            rate_limit = api.get_rate_limit().core
            logging.info("Rate limit remaining: %d.", rate_limit.remaining)
            reset_timestamp = calendar.timegm(rate_limit.reset.timetuple())
            sleep_time = reset_timestamp - calendar.timegm(time.gmtime()) + _TIME_DELTA
            logging.info("Waiting %dmin(s) %dsec(s) until rate limit resets.", int(sleep_time / 60), sleep_time % 60)
            time.sleep(sleep_time)
            data[repo.name] = _get_repo_data(repo, default_contrib, default_coc, bot_user)
            continue
    total_repos_seen = num_archived + len(data.keys())
    logging.info(
        "Summary: %d archived | %d to write into CSV file | %d total repos in %s.",
        num_archived,
        total_repos_seen,
        num_repos_in_org,
        org_name,
    )
    assert total_repos_seen == org_repos.totalCount, f"Got {total_repos_seen} repos but expected {num_repos_in_org}."
    return data


def _get_repo_data(
    repo: Repository, default_contrib: Optional[str], default_coc: Optional[str], bot_user: Optional[str]
) -> RepoDataType:
    """Return a dict of repository information for one repo.

    Note that the dictionary keys here need to match those in _write_csv_file().
    """
    repo_info: RepoDataType = {}
    repo_info["name"] = repo.name
    repo_info["is archived"] = repo.archived
    repo_info["is private"] = repo.private
    repo_info["is fork"] = repo.fork
    repo_info["created at"] = repo.created_at
    repo_info["pushed at"] = repo.pushed_at
    repo_info["default branch"] = repo.default_branch
    default_branch = repo.get_branch(repo.default_branch)
    repo_info["commits on default branch"] = repo.get_commits(sha=default_branch.name).totalCount
    last_commit = default_branch.raw_data["commit"]["commit"]["committer"]["date"]
    repo_info["last commit to default branch"] = datetime.strptime(last_commit, _DATETIME_FORMAT)
    branches = repo.get_branches()
    has_master_branch = "master" in [branch.name for branch in branches]
    has_main_branch = "main" in [branch.name for branch in branches]
    repo_info["has master branch but no main"] = has_master_branch and not has_main_branch
    try:
        repo.get_license()
        repo_info["has license file"] = True
    except UnknownObjectException:
        repo_info["has license file"] = False
    repo_info["contributing relates to org default"] = _get_relationship_to_org_default(
        default_contrib, _CONTRIBUTING, repo
    ).value
    repo_info["coc relates to org default"] = _get_relationship_to_org_default(
        default_coc, _CODE_OF_CONDUCT, repo
    ).value
    is_private_but_has_no_why_private = False
    if repo_info["is private"]:
        is_private_but_has_no_why_private = _get_file_contents(repo, _WHY_PRIVATE) is None
    repo_info["missing why private"] = is_private_but_has_no_why_private
    repo_info["uses Travis CI"] = _get_file_contents(repo, _TRAVIS_CI_CONFIG) is not None
    repo_info["topics"] = ", ".join(repo.get_topics())
    teams = []
    try:
        teams = [team.name for team in repo.get_teams()]
    except UnknownObjectException:
        pass
    repo_info["teams"] = ", ".join(teams)
    repo_info["forks count"] = repo.forks_count
    repo_info["open issues"] = repo.open_issues_count
    pulls = repo.get_pulls("open")
    repo_info["open prs"] = pulls.totalCount
    has_automated_pr = False
    has_configure_renovate_pr = False
    for pull in pulls:
        if pull.title == _RENOVATE_CONFIGURE_PR and pull.user.login == _RENOVATE_USER:
            has_configure_renovate_pr = True
        if bot_user is not None:
            if pull.user.login == bot_user:
                has_automated_pr = True
        if (has_configure_renovate_pr and has_automated_pr) or (has_configure_renovate_pr and bot_user is None):
            break
    repo_info[f"has unmerged {_RENOVATE_CONFIGURE_PR} PR"] = has_configure_renovate_pr
    repo_info["has unmerged PR(s) from org bot"] = has_automated_pr
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
    """Get the default version of filename for a given organisation."""

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
        assert isinstance(content_file, ContentFile), f"{filename} does not seem to be a file. Is it a directory?"
        contents = content_file.decoded_content.decode()
    except UnknownObjectException:
        pass
    return contents


def _write_csv_file(github_data: OrgDataType, csvfile: str) -> None:
    """Write out a CSV file containing the repo_data dictionary."""

    headers: list[str] = [
        "name",
        "is archived",
        "is private",
        "is fork",
        "created at",
        "pushed at",
        "default branch",
        "commits on default branch",
        "last commit to default branch",
        "open issues",
        "open prs",
        "has master branch but no main",
        "has license file",
        "contributing relates to org default",
        "coc relates to org default",
        "missing why private",
        f"has unmerged {_RENOVATE_CONFIGURE_PR} PR",
        "has unmerged PR(s) from org bot",
        "uses Travis CI",
        "forks count",
        "teams",
        "topics",
    ]
    if github_data:
        actual_headers = list(github_data[list(github_data)[0]])
        logging.debug("Expected headers: %s", repr(sorted(headers)))
        logging.debug("Got headers:%s", repr(sorted(actual_headers)))
        diff = sorted(set(headers).symmetric_difference(actual_headers))
        assert sorted(actual_headers) == sorted(
            headers
        ), f"You found a bug! Data does not have expected keys. This was the diff:{repr(diff)}"
    with open(csvfile, "wt", encoding="Utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(headers)
        for repo in github_data.values():
            row = [repo[column] for column in headers]
            writer.writerow(row)
    logging.info("CSV data written to %s.", csvfile)


def _create_parser() -> ArgumentParser:
    """Create a parser for command line arguments."""

    parser = ArgumentParser(description=__doc__)
    # Required, positional arguments.
    apikey_help = "GitHub personal access token: https://github.com/settings/tokens"
    parser.add_argument("apikey", action="store", type=str, help=apikey_help)
    org_help = "Name of the GitHub organisation to be audited: https://docs.github.com/en/organizations"
    parser.add_argument("org", action="store", type=str, default="", help=org_help)
    # Optional arguments.
    bot_user_help = (
        "Optional username of a bot that creates automated PRs for standards compliance within the organization"
    )
    parser.add_argument("-b", "--bot-user", action="store", type=str, default=None, help=bot_user_help)
    csvfile_help = f"Name of the CSV file to be written out, defaults to: {_DEFAULT_OUTPUT_FILENAME}"
    parser.add_argument(
        "-c",
        "--csvfile",
        action="store",
        type=str,
        default=_DEFAULT_OUTPUT_FILENAME,
        help=csvfile_help,
    )
    loglevel_help = f"Logging level, defaults to {_DEFAULT_LOGLEVEL}."
    valid_loglevels = list(logging._nameToLevel.keys())  # pylint: disable=W0212
    parser.add_argument(
        "-l",
        "--loglevel",
        action="store",
        type=str,
        default=_DEFAULT_LOGLEVEL,
        choices=valid_loglevels,
        help=loglevel_help,
    )
    return parser


if __name__ == "__main__":
    options = _create_parser().parse_args()
    logging.basicConfig(encoding="utf-8", level=options.loglevel)
    repo_data = _get_github_data(options.apikey, options.org, options.bot_user)
    _write_csv_file(repo_data, options.csvfile)
