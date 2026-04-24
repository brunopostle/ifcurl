// Copyright 2026 The Forgejo Authors. All rights reserved.
// SPDX-License-Identifier: MIT

// ifc_url.go adds preview rendering for ifc:// URLs in Markdown.
//
// Configuration (app.ini):
//
//	[ifcurl]
//	PREVIEW_SERVICE_URL = http://localhost:8000
//
// Integration in markdown.go — add to goldmark.WithExtensions(...):
//
//	NewIfcURLExtension(),

package markdown

import (
	"bytes"
	"fmt"
	"net/url"
	"strings"

	"forgejo.org/modules/setting"

	"github.com/yuin/goldmark"
	"github.com/yuin/goldmark/ast"
	"github.com/yuin/goldmark/parser"
	"github.com/yuin/goldmark/renderer"
	"github.com/yuin/goldmark/text"
	"github.com/yuin/goldmark/util"
)

var ifcURLKind = ast.NewNodeKind("IfcURL")

type ifcURLNode struct {
	ast.BaseInline
	rawURL string
}

func (n *ifcURLNode) Kind() ast.NodeKind { return ifcURLKind }
func (n *ifcURLNode) Dump(src []byte, level int) {
	ast.DumpHelper(n, src, level, map[string]string{"URL": n.rawURL}, nil)
}

// ifcURLTransformer walks the parsed AST and replaces ifc:// occurrences with
// ifcURLNode.  It handles both [title](ifc://...) links and bare ifc:// URLs.
//
// Bare URLs may span multiple consecutive text-like siblings because goldmark's
// emphasis parser splits text nodes at delimiter characters (e.g. the '_' in
// "?path=_test.ifc"), so we collect siblings until a URL terminator is found.
type ifcURLTransformer struct{}

// urlTerminators end a bare ifc:// URL in inline text.
const urlTerminators = " \t\n\r<>\"'"

func (t *ifcURLTransformer) Transform(doc *ast.Document, reader text.Reader, _ parser.Context) {
	if setting.IfcURL.PreviewServiceURL == "" {
		return
	}
	src := reader.Source()

	// Replace [title](ifc://...) link nodes.
	var links []*ast.Link
	if err := ast.Walk(doc, func(n ast.Node, entering bool) (ast.WalkStatus, error) {
		if !entering {
			return ast.WalkContinue, nil
		}
		if link, ok := n.(*ast.Link); ok && strings.HasPrefix(string(link.Destination), "ifc://") {
			links = append(links, link)
		}
		return ast.WalkContinue, nil
	}); err != nil {
		return
	}
	for _, link := range links {
		link.Parent().ReplaceChild(link.Parent(), link, &ifcURLNode{rawURL: string(link.Destination)})
	}

	// Replace bare ifc:// URLs in inline text nodes.
	type span struct {
		parent   ast.Node
		first    ast.Node
		spanned  []ast.Node // siblings consumed beyond first (last element is the final node)
		rawURL   string
		before   []byte // text in first node before "ifc://"
		trailing []byte // text in last node after the URL end
	}

	var spans []span
	claimed := map[ast.Node]bool{}

	if err := ast.Walk(doc, func(n ast.Node, entering bool) (ast.WalkStatus, error) {
		if !entering || claimed[n] {
			return ast.WalkContinue, nil
		}
		tb, ok := inlineText(n, src)
		if !ok {
			return ast.WalkContinue, nil
		}
		idx := bytes.Index(tb, []byte("ifc://"))
		if idx < 0 {
			return ast.WalkContinue, nil
		}

		before := tb[:idx]
		urlBytes := append([]byte(nil), tb[idx:]...)
		var spanned []ast.Node
		var trailing []byte

		if end := bytes.IndexAny(urlBytes, urlTerminators); end >= 0 {
			trailing = append([]byte(nil), urlBytes[end:]...)
			urlBytes = urlBytes[:end]
		} else {
			for sib := n.NextSibling(); sib != nil; sib = sib.NextSibling() {
				sibTB, ok := inlineText(sib, src)
				if !ok {
					break
				}
				spanned = append(spanned, sib)
				if end := bytes.IndexAny(sibTB, urlTerminators); end >= 0 {
					urlBytes = append(urlBytes, sibTB[:end]...)
					trailing = append([]byte(nil), sibTB[end:]...)
					break
				}
				urlBytes = append(urlBytes, sibTB...)
			}
		}

		if len(urlBytes) <= len("ifc://") {
			return ast.WalkContinue, nil
		}

		spans = append(spans, span{
			parent:   n.Parent(),
			first:    n,
			spanned:  spanned,
			rawURL:   string(urlBytes),
			before:   append([]byte(nil), before...),
			trailing: trailing,
		})
		claimed[n] = true
		for _, s := range spanned {
			claimed[s] = true
		}
		return ast.WalkContinue, nil
	}); err != nil {
		return
	}

	for _, s := range spans {
		parent := s.parent
		if parent == nil {
			continue
		}
		last := s.first
		if len(s.spanned) > 0 {
			last = s.spanned[len(s.spanned)-1]
		}

		// Remove intermediate spanned nodes; replace or remove the last one.
		for _, m := range s.spanned {
			if m == last {
				continue
			}
			parent.RemoveChild(parent, m)
		}
		if last != s.first {
			if len(s.trailing) > 0 {
				parent.ReplaceChild(parent, last, ast.NewString(s.trailing))
			} else {
				parent.RemoveChild(parent, last)
			}
		}

		// Build replacement nodes for first: [before?] ifcURLNode [trailing if single-node?]
		ifcNode := &ifcURLNode{rawURL: s.rawURL}
		seq := make([]ast.Node, 0, 3)
		if len(s.before) > 0 {
			seq = append(seq, ast.NewString(s.before))
		}
		seq = append(seq, ifcNode)
		if last == s.first && len(s.trailing) > 0 {
			seq = append(seq, ast.NewString(s.trailing))
		}

		parent.ReplaceChild(parent, s.first, seq[0])
		prev := seq[0]
		for _, nn := range seq[1:] {
			parent.InsertAfter(parent, prev, nn)
			prev = nn
		}
	}
}

// inlineText returns the raw bytes of an ast.Text or ast.String node.
func inlineText(n ast.Node, src []byte) ([]byte, bool) {
	switch s := n.(type) {
	case *ast.Text:
		seg := s.Segment
		return src[seg.Start:seg.Stop], true
	case *ast.String:
		return s.Value, true
	}
	return nil, false
}

type ifcURLRenderer struct {
	previewServiceURL string
	serviceToken      string
}

func (r *ifcURLRenderer) RegisterFuncs(reg renderer.NodeRendererFuncRegisterer) {
	reg.Register(ifcURLKind, r.render)
}

func (r *ifcURLRenderer) render(w util.BufWriter, _ []byte, node ast.Node, entering bool) (ast.WalkStatus, error) {
	if !entering {
		return ast.WalkSkipChildren, nil
	}
	n := node.(*ifcURLNode)

	previewURL := r.previewServiceURL + "/preview?url=" + url.QueryEscape(n.rawURL)
	if r.serviceToken != "" {
		previewURL += "&token=" + url.QueryEscape(r.serviceToken)
	}
	viewerURL := "/assets/viewer.html?url=" + url.QueryEscape(n.rawURL)
	safeIfc := htmlEscapeString(n.rawURL)
	safePreview := htmlEscapeString(previewURL)
	safeViewer := htmlEscapeString(viewerURL)

	fmt.Fprintf(w,
		`<figure class="ifcurl-preview">`+
			`<a href="%s" title="%s">`+
			`<img src="%s" alt="IFC preview" loading="lazy" style="max-width:100%%"/>`+
			`</a>`+
			`<figcaption><code>%s</code></figcaption>`+
			`</figure>`,
		safeViewer, safeIfc, safePreview, safeIfc,
	)
	return ast.WalkSkipChildren, nil
}

func htmlEscapeString(s string) string {
	s = strings.ReplaceAll(s, "&", "&amp;")
	s = strings.ReplaceAll(s, "<", "&lt;")
	s = strings.ReplaceAll(s, ">", "&gt;")
	s = strings.ReplaceAll(s, `"`, "&#34;")
	s = strings.ReplaceAll(s, "'", "&#39;")
	return s
}

type ifcURLExtension struct {
	previewServiceURL string
	serviceToken      string
}

func NewIfcURLExtension() goldmark.Extender {
	return &ifcURLExtension{
		previewServiceURL: setting.IfcURL.PreviewServiceURL,
		serviceToken:      setting.IfcURL.ServiceToken,
	}
}

func (e *ifcURLExtension) Extend(m goldmark.Markdown) {
	m.Parser().AddOptions(
		parser.WithASTTransformers(
			util.Prioritized(&ifcURLTransformer{}, 999),
		),
	)
	m.Renderer().AddOptions(
		renderer.WithNodeRenderers(
			util.Prioritized(&ifcURLRenderer{
				previewServiceURL: e.previewServiceURL,
				serviceToken:      e.serviceToken,
			}, 999),
		),
	)
}
