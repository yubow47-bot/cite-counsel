import logging, time
logging.basicConfig(level=logging.DEBUG, format="%(message)s")
from dotenv import load_dotenv
load_dotenv()
from local_tools.citation_search import classify_and_normalize, search_citation
from core.mcgill_engine import format_citation

t0 = time.time()
c = classify_and_normalize("r v oakes")
t1 = time.time()
r = search_citation("r v oakes", c)
t2 = time.time()
cit = format_citation({k: v for k, v in r[0].items() if not k.startswith("_")})
t3 = time.time()

print("CLASSIFY:", round(t1-t0, 2), "s")
print("SEARCH:", round(t2-t1, 2), "s")
print("FORMAT:", round(t3-t2, 2), "s")
print("TOTAL:", round(t3-t0, 2), "s")
print("CITATION:", cit)
