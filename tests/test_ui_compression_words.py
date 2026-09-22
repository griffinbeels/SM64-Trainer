"""The compression list's wording, byte sizes and shared poller, browser-free."""
from frontend_runner import run_frontend


def test_compression_words_and_the_shared_poller():
    run_frontend("compression.test.js")
