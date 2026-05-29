PDF = main.pdf
TEX = main.tex
SECTIONS = $(wildcard sections/*.tex)

.PHONY: all clean watch open wordcount

all: $(PDF)

$(PDF): $(TEX) $(SECTIONS)
	latexmk -pdf -interaction=nonstopmode -halt-on-error $(TEX)

watch:
	latexmk -pdf -pvc -interaction=nonstopmode $(TEX)

open: $(PDF)
	open $(PDF)

wordcount:
	@texcount -inc -sum=1 $(TEX) 2>/dev/null || echo "install texcount (brew install texcount)"

clean:
	latexmk -C
	rm -f *.aux *.log *.out *.toc *.fdb_latexmk *.fls *.synctex.gz \
	      sections/*.aux
