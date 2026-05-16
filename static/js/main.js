/* ENOROSSI Paint Optimizer v5 */
(function(){
  // Clock topbar
  function tick(){
    const el=document.getElementById('clock');
    if(el){const n=new Date();el.textContent=n.toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit',second:'2-digit'});}
  }
  tick();setInterval(tick,1000);

  // Sidebar toggle mobile
  window.toggleSb=function(){document.getElementById('sb').classList.toggle('open');};

  // Chart defaults
  if(typeof Chart!=='undefined'){
    Chart.defaults.color='#555';
    Chart.defaults.borderColor='rgba(255,255,255,.06)';
  }

  // Upload 3D
  window.upload3D=async function(codice){
    const fi=document.getElementById('file3d_'+codice);
    const btn=document.getElementById('btn3d_'+codice);
    const res=document.getElementById('result3d_'+codice);
    if(!fi||!fi.files[0]){alert('Seleziona un file 3D.');return;}
    btn.disabled=true;btn.textContent='Analisi...';
    const fd=new FormData();fd.append('file_3d',fi.files[0]);fd.append('codice',codice);
    try{
      const r=await fetch('/api/analizza_3d',{method:'POST',body:fd});
      const d=await r.json();
      if(d.ok) res.innerHTML=`<div class="alert alert-ok mt-2">✅ ${d.lunghezza_mm}×${d.larghezza_mm}×${d.altezza_mm}mm · Sup: ${d.superficie_m2}m² · Peso: ${d.peso_kg}kg</div>`;
      else res.innerHTML=`<div class="alert alert-warn mt-2">⚠️ ${d.error}</div>`;
    }catch(e){res.innerHTML=`<div class="alert alert-err mt-2">❌ ${e}</div>`;}
    btn.disabled=false;btn.textContent='Analizza';
  };
})();
