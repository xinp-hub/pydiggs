# Installation

## Prerequisites

pydiggs requires Python 3.8 or later and depends on the following packages:
* lxml - for XML processing and validation
* rich - for colored console output

## Stable Release

To install pydiggs, run this command in your terminal:

```bash
pip install pydiggs
```

This is the preferred method to install pydiggs, as it will always install the most recent stable release with all required dependencies.

If you don't have [pip](https://pip.pypa.io) installed, this [Python installation guide](https://docs.python-guide.org/starting/installation/) can guide you through the process.

## Development Version

The latest development version can be installed directly from GitHub:

```bash
pip install git+https://github.com/xinp-hub/pydiggs.git
```

## From Sources

The sources for pydiggs can be downloaded from the [GitHub repo](https://github.com/xinp-hub/pydiggs).

You can either clone the public repository:

```bash
git clone git://github.com/xinp-hub/pydiggs
```

Or download the [tarball](https://github.com/xinp-hub/pydiggs/tarball/master):

```bash
curl -OJL https://github.com/xinp-hub/pydiggs/tarball/master
```

Once you have a copy of the source, you can install it with:

```bash
# If you have Poetry installed (recommended for development)
poetry install

# Or using pip
pip install .
```

## Verification

After installation, you can verify that pydiggs is installed correctly by running:

```bash
python -c "import pydiggs; print(pydiggs.__version__)"
```

This should print the version number of your installed pydiggs package. 