from setuptools import setup

setup(
    name="editdistance",
    version="0.8.1",
    description="Pure Python Levenshtein distance (fallback for C extension)",
    py_modules=["editdistance"],
    package_dir={"": "."},
)
