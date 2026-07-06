import logging, time
logging.basicConfig(level=logging.DEBUG, format="%(message)s")
from dotenv import load_dotenv
load_dotenv()
from local_tools.citation_search import classify_and_normalize, search_citation
t0 = time.time()
c = classify_and_normalize("immigration and refugee protection act")
print("CLASSIFY:", c)
r = search_citation("immigration and refugee protection act", c)
print("RESULT:", r)
print("TOTAL:", time.time()-t0, "s")
