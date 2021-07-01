#!/usr/bin/env python

"""The setup script."""

from setuptools import setup, find_packages

with open('README.rst') as readme_file:
    readme = readme_file.read()

with open('HISTORY.rst') as history_file:
    history = history_file.read()

with open('requirements.txt') as requirements_file:
    requirements = requirements_file.read().splitlines()

test_requirements = ['pytest>=3', ]

setup(
    author="Xin Peng",
    author_email='xin_peng@outlook.com',
    python_requires='>=3.6',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
    ],
    description="A Python package for Data Interchange for Geotechnical and Geoenvironmental Specialists (DIGGS).",
    entry_points={
        'console_scripts': [
            'pydiggs=pydiggs.cli:main',
        ],
    },
    install_requires=requirements,
    license="GNU General Public License v3",
    long_description=readme + '\n\n' + history,
    include_package_data=True,
    keywords='pydiggs',
    name='pydiggs',
    packages=find_packages(include=['pydiggs', 'pydiggs.*']),
    test_suite='tests',
    tests_require=test_requirements,
    url='https://github.com/xinp-hub/pydiggs',
    version='0.1.2',
    zip_safe=False,
)

