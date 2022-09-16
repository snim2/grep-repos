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