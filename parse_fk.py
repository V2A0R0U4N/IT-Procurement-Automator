from bs4 import BeautifulSoup
with open('fk_prod.html') as f:
    soup = BeautifulSoup(f, 'html.parser')

print("TITLE:", soup.find('h1'))
price_str = None
for el in soup.find_all(lambda tag: tag.name == 'div' and '₹' in tag.get_text()):
    classes = el.get('class')
    if classes and len(classes) > 0 and '₹' in el.get_text()[:3]:
        print("PRICE DIV:", el.get_text()[:20], classes)

# Specs
print("SPECS ROWS:", len(soup.find_all('tr')))
print("SPECS LI:", len(soup.find_all('li')))
