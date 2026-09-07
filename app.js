const products=[
  {name:'Magnetic Creator Phone Mount',price:29.99,icon:'📱',desc:'A compact magnetic mount designed for hands-free creator setups.',scores:{demand:63,margin:88,competition:72,shipping:92,demo:96,bundle:90}},
  {name:'45W Magnetic Charging Kit',price:34.99,icon:'⚡',desc:'Fast everyday charging hardware built for a clean desk setup.',scores:{demand:63,margin:84,competition:70,shipping:86,demo:91,bundle:94}},
  {name:'6-in-1 USB-C Desk Hub',price:39.99,icon:'🔌',desc:'A practical expansion hub for modern laptops and creator desks.',scores:{demand:63,margin:82,competition:66,shipping:78,demo:88,bundle:92}},
  {name:'Smart Cable Organizer Set',price:16.99,icon:'🧩',desc:'Simple cable control for cleaner desks and easier workflows.',scores:{demand:63,margin:90,competition:76,shipping:98,demo:84,bundle:96}}
];

const weights={demand:.25,margin:.20,competition:.15,shipping:.10,demo:.15,bundle:.15};

function opportunityScore(scores){
  return Math.round(Object.entries(weights).reduce((total,[key,weight])=>{
    const value=scores[key] ?? 0;
    const normalized=key==='competition' ? 100-value : value;
    return total+(normalized*weight);
  },0));
}

products.forEach(p=>p.opportunityScore=opportunityScore(p.scores));
products.sort((a,b)=>b.opportunityScore-a.opportunityScore);

let cart=0;
const grid=document.getElementById('productGrid');

products.forEach((p,i)=>{
  const el=document.createElement('article');
  el.className='product';
  el.innerHTML=`<div class="icon">${p.icon}</div><div class="score">Radar ${p.opportunityScore}/100</div><h3>${p.name}</h3><p>${p.desc}</p><div class="price">$${p.price.toFixed(2)}</div><button class="add" data-id="${i}">Add to cart</button>`;
  grid.appendChild(el);
});

grid.addEventListener('click',e=>{
  if(!e.target.matches('.add'))return;
  cart++;
  document.getElementById('cartCount').textContent=cart;
  e.target.textContent='Added ✓';
  setTimeout(()=>e.target.textContent='Add to cart',900);
});

const ranked=products.map(p=>p.opportunityScore);
const avg=Math.round(ranked.reduce((a,b)=>a+b,0)/ranked.length);
const radarHeading=document.querySelector('#radar h2 span');
if(radarHeading)radarHeading.textContent=`${avg}/100`;
