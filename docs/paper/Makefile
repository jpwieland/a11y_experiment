# Makefile for compiling the a11y-autofix academic paper
# Requires: pdflatex (or xelatex), bibtex
# Install on macOS: brew install --cask mactex  (or brew install basictex)

PAPER    := paper
TEXFILE  := $(PAPER).tex
PDFFILE  := $(PAPER).pdf

# Use pdflatex by default; override with: make LATEX=xelatex
LATEX    := pdflatex
LATEXFLAGS := -interaction=nonstopmode -halt-on-error

.PHONY: all clean distclean view

all: $(PDFFILE)

$(PDFFILE): $(TEXFILE)
	$(LATEX) $(LATEXFLAGS) $(TEXFILE)
	$(LATEX) $(LATEXFLAGS) $(TEXFILE)
	$(LATEX) $(LATEXFLAGS) $(TEXFILE)

clean:
	rm -f *.aux *.log *.bbl *.blg *.toc *.lof *.lot *.out \
	       *.fls *.fdb_latexmk *.synctex.gz *.nav *.snm *.vrb

distclean: clean
	rm -f $(PDFFILE)

view: $(PDFFILE)
	open $(PDFFILE) 2>/dev/null || xdg-open $(PDFFILE) 2>/dev/null || \
	    echo "Could not open PDF. Run: open $(PDFFILE)"
