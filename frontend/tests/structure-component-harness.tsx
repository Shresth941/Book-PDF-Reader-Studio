// Synthetic component fixture: no PDF parser, worker, local document or network API.
import React, {useState} from "react";
import {createRoot} from "react-dom/client";
import {ManualTableEditor, ManualTableInput} from "../src/ManualTableEditor";
import {FormEvidenceReview, FormEvidencePreview} from "../src/FormEvidenceReview";
import {ReviewPage, Region} from "../src/reviewApi";
import "../src/review.css";

const region = (id: string, text: string, bbox: Region["bbox"], order: number): Region => ({
  id, order, bbox, kind:"paragraph", raw:text, source:text, translation:"", source_language:"uncertain", language_hint:"latin",
  disposition:text ? "translate" : "unreadable", contains_text:true, reason:"", method:"native", source_approved:false,
  translation_approved:false, acknowledged:{}, evidence:[], alternatives:[], checks:[],
});
const fixture: ReviewPage = {number:1, revision:1, status:"extracted", width:600, height:800, coverage_approved:false, blockers:[], error:"",
  regions:[region("name", "Name", [60,90,90,110],0),region("amount", "Amount", [160,90,200,110],1),region("alice", "Alice", [60,140,90,160],2)],
  table_proposals:{status:"none",tables:[],warnings:[]}};
const image = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="white"/><path d="M50 80H250V180H50Z M150 80V180 M50 130H250" fill="none" stroke="black"/><g font-family="sans-serif" font-size="14"><text x="60" y="105">Name</text><text x="160" y="105">Amount</text><text x="60" y="155">Alice</text></g></svg>');
function Harness() {
  const [page,setPage]=useState(fixture), [result,setResult]=useState<ManualTableInput>();
  const [fail,setFail]=useState(false), [formRevision,setFormRevision]=useState(1), [formResult,setFormResult]=useState<unknown>();
  const field = {...region("saved-field","Visible original",[30,30,150,70],0),method:"form-field",source_approved:true,
    form_review_note:"Previously reviewed original appearance",form_evidence:{status:"contradiction",xref:8,stored_value:"Stored newer value",appearance:{present:true,text:"Visible original"},discrepancies:[]}};
  const formPage={...fixture,revision:formRevision,regions:[field]};
  return <main className="review-app" style={{padding:20,maxWidth:1000,margin:"auto"}}><h1>Synthetic structure component checks</h1>
    <p>No document processing or API requests. This fixture only tests browser controls.</p>
    <label className="checkbox-label"><input type="checkbox" checked={fail} onChange={e=>setFail(e.target.checked)}/>Simulate save failure</label>
    <button onClick={()=>setPage(p=>({...p,revision:p.revision+1}))}>Simulate table revision change</button>
    <ManualTableEditor page={page} previewUrl={image} draftKey="synthetic-grid-component-draft" disabled={false} onCreate={async body=>{if(fail)throw Error("Synthetic save failure; retain draft");setResult(body);setPage(p=>({...p,revision:p.revision+1}));}}/>
    <pre style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}} aria-label="Submitted manual grid">{result ? JSON.stringify(result):"No grid submitted"}</pre>
    <button onClick={()=>setFormRevision(v=>v+1)}>Simulate form revision change</button>
    <FormEvidenceReview page={formPage} busy={false} onPreview={async()=>({id:"preview",page_revision:formRevision,document_revision:1,
      inspection:{regions:[field],form_evidence:{widgets:[field.form_evidence],inventory_complete:true}},suggestions:{"8":"saved-field"}} as FormEvidencePreview)}
      onApply={async(preview,bindings,note)=>{if(fail)throw Error("Synthetic form save failure; retain preview");setFormResult({preview,bindings,note,source:field.source,approved:field.source_approved});}}/>
    <pre style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}} aria-label="Submitted form mappings">{formResult ? JSON.stringify(formResult):"No form mappings submitted"}</pre>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Harness/>);
