# grep-repos

Generate a CSV file of information about a set of GitHub repositories.

## Getting started

Install dependencies:

```shell
./script/bootstrap
```

Add these lines to your `~/.zshrc` or similar:

```shell
eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"
```

The Python virtualenv that the `script/bootstrap` script created will then be automatically activated and deactivated whenever you enter/leave the root of the repository.

## Linting the code

Run:

```shell
./script/test
```

to run a standard set of lints over the code here.

## Installing Git hooks

This repository contains a Git `pre-commit` hook that runs `shellcheck` over the shell
scripts and calls `./script/test`. To install the Git hook, run:

```shell
./script/install-git-hooks
```
