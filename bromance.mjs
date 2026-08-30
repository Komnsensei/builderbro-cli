// bromance.mjs — BROs Skill Connector Engine
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { join } from "path";
import { homedir } from "os";
import { createHash } from "crypto";

var DATA=join(homedir(),".bro");
var BROFICIENCIES_F=join(DATA,"broficiencies.json");
var CHAIN_F=join(DATA,"chain.json");
var CONTEXT_DIR=join(DATA,"contexts");
try{mkdirSync(DATA,{recursive:true});mkdirSync(CONTEXT_DIR,{recursive:true});}catch{}

var broficiencies;
try{broficiencies=JSON.parse(readFileSync(BROFICIENCIES_F,"utf8"));}catch{broficiencies={installed:[],history:[]};}
function saveBroficiencies(){try{writeFileSync(BROFICIENCIES_F,JSON.stringify(broficiencies,null,2),"utf8");}catch{}}

// --- Persona State ---
var _currentPersona = null;

export function setCurrentPersona(persona) {
  _currentPersona = persona;
  globalThis.__currentPersona = persona;
  console.log(globalThis.p("dim","  BRO: Active Persona: "+(persona ? persona.name : "Default")));
  console.log(globalThis.p("dim","    Temp: "+(persona ? persona.temperature.toFixed(1) : "N/A")+", YOLO: "+(persona ? persona.yoloMode : "N/A")));
}

export function getCurrentPersona() {
  return _currentPersona;
}

var REGISTRIES={
mcp:[
{id:"mcp-filesystem",name:"Filesystem MCP",url:"npx -y @modelcontextprotocol/server-filesystem",type:"mcp",desc:"File ops",category:"core",free:true},
{id:"mcp-github",name:"GitHub MCP",url:"npx -y @modelcontextprotocol/server-github",type:"mcp",desc:"Repos, issues, PRs",category:"devops",free:true,needs:"GITHUB_TOKEN"},
{id:"mcp-gitlab",name:"GitLab MCP",url:"npx -y @modelcontextprotocol/server-gitlab",type:"mcp",desc:"GitLab",category:"devops",free:true,needs:"GITLAB_TOKEN"},
{id:"mcp-postgres",name:"Postgres MCP",url:"npx -y @modelcontextprotocol/server-postgres",type:"mcp",desc:"Postgres queries",category:"data",free:true},
{id:"mcp-sqlite",name:"SQLite MCP",url:"npx -y @modelcontextprotocol/server-sqlite",type:"mcp",desc:"SQLite",category:"data",free:true},
{id:"mcp-mysql",name:"MySQL MCP",url:"npx -y @modelcontextprotocol/server-mysql",type:"mcp",desc:"MySQL",category:"data",free:true},
{id:"mcp-mongodb",name:"MongoDB MCP",url:"npx -y mcp-mongo-server",type:"mcp",desc:"MongoDB",category:"data",free:true},
{id:"mcp-redis",name:"Redis MCP",url:"npx -y @modelcontextprotocol/server-redis",type:"mcp",desc:"Redis",category:"data",free:true},
{id:"mcp-brave",name:"Brave Search MCP",url:"npx -y @modelcontextprotocol/server-brave-search",type:"mcp",desc:"Web search",category:"search",free:true,needs:"BRAVE_KEY"},
{id:"mcp-puppeteer",name:"Puppeteer MCP",url:"npx -y @modelcontextprotocol/server-puppeteer",type:"mcp",desc:"Browser auto",category:"web",free:true},
{id:"mcp-playwright",name:"Playwright MCP",url:"npx -y @playwright/mcp",type:"mcp",desc:"Cross-browser",category:"web",free:true},
{id:"mcp-memory",name:"Memory MCP",url:"npx -y @modelcontextprotocol/server-memory",type:"mcp",desc:"Knowledge graph",category:"cognitive",free:true},
{id:"mcp-fetch",name:"Fetch MCP",url:"npx -y @modelcontextprotocol/server-fetch",type:"mcp",desc:"HTTP fetch",category:"web",free:true},
{id:"mcp-slack",name:"Slack MCP",url:"npx -y @modelcontextprotocol/server-slack",type:"mcp",desc:"Slack",category:"social",free:true,needs:"SLACK_TOKEN"},
{id:"mcp-discord",name:"Discord MCP",url:"npx -y mcp-discord",type:"mcp",desc:"Discord",category:"social",free:true,needs:"DISCORD_TOKEN"},
{id:"mcp-telegram",name:"Telegram MCP",url:"npx -y telegram-mcp",type:"mcp",desc:"Telegram",category:"social",free:true,needs:"TELEGRAM_TOKEN"},
{id:"mcp-notion",name:"Notion MCP",url:"npx -y @notionhq/notion-mcp-server",type:"mcp",desc:"Notion",category:"productivity",free:true,needs:"NOTION_TOKEN"},
{id:"mcp-linear",name:"Linear MCP",url:"npx -y @tacticlaunch/mcp-linear",type:"mcp",desc:"Linear",category:"productivity",free:true,needs:"LINEAR_TOKEN"},
{id:"mcp-jira",name:"Jira MCP",url:"npx -y @cosmix/jira-mcp",type:"mcp",desc:"Jira",category:"productivity",free:true,needs:"JIRA_TOKEN"},
{id:"mcp-trello",name:"Trello MCP",url:"npx -y @delorenj/mcp-trello",type:"mcp",desc:"Trello",category:"productivity",free:true,needs:"TRELLO_KEY"},
{id:"mcp-docker",name:"Docker MCP",url:"npx -y @jonyinc/mcp-docker",type:"mcp",desc:"Docker",category:"devops",free:true},
{id:"mcp-k8s",name:"Kubernetes MCP",url:"npx -y kubernetes-mcp-server",type:"mcp",desc:"K8s",category:"devops",free:true},
{id:"mcp-cloudflare",name:"Cloudflare MCP",url:"npx -y @cloudflare/mcp-server-cloudflare",type:"mcp",desc:"Workers, KV, D1",category:"cloud",free:true,needs:"CF_TOKEN"},
{id:"mcp-supabase",name:"Supabase MCP",url:"npx -y @supabase/mcp-server-supabase",type:"mcp",desc:"DB, auth",category:"data",free:true,needs:"SUPABASE_URL"},
{id:"mcp-stripe",name:"Stripe MCP",url:"npx -y @stripe/mcp",type:"mcp",desc:"Payments",category:"commerce",free:true,needs:"STRIPE_KEY"},
{id:"mcp-sentry",name:"Sentry MCP",url:"npx -y @modelcontextprotocol/server-sentry",type:"mcp",desc:"Errors",category:"devops",free:true,needs:"SENTRY_TOKEN"},
{id:"mcp-gdrive",name:"GDrive MCP",url:"npx -y @modelcontextprotocol/server-gdrive",type:"mcp",desc:"Drive",category:"cloud",free:true,needs:"GOOGLE_CREDS"},
{id:"mcp-gmail",name:"Gmail MCP",url:"npx -y @gongrzhe/server-gmail-autoauth-mcp",type:"mcp",desc:"Gmail",category:"comms",free:true,needs:"GMAIL_CREDS"},
{id:"mcp-time",name:"Time MCP",url:"npx -y mcp-server-time",type:"mcp",desc:"Time/tz",category:"core",free:true},
{id:"mcp-vercel",name:"Vercel MCP",url:"npx -y @nganiet/mcp-vercel",type:"mcp",desc:"Vercel",category:"cloud",free:true,needs:"VERCEL_TOKEN"},
{id:"mcp-figma",name:"Figma MCP",url:"npx -y figma-developer-mcp",type:"mcp",desc:"Figma to code",category:"design",free:true,needs:"FIGMA_TOKEN"},
{id:"mcp-tavily",name:"Tavily MCP",url:"npx -y tavily-mcp",type:"mcp",desc:"AI search",category:"search",free:true,needs:"TAVILY_KEY"},
{id:"mcp-firecrawl",name:"Firecrawl MCP",url:"npx -y firecrawl-mcp",type:"mcp",desc:"Scraping",category:"web",free:true,needs:"FIRECRAWL_KEY"},
{id:"mcp-youtube",name:"YouTube MCP",url:"npx -y @anaisbetts/mcp-youtube",type:"mcp",desc:"Transcripts",category:"media",free:true},
{id:"mcp-twitter",name:"Twitter MCP",url:"npx -y @enescinar/twitter-mcp",type:"mcp",desc:"Tweets",category:"social",free:true,needs:"TWITTER_BEARER"},
{id:"mcp-reddit",name:"Reddit MCP",url:"npx -y mcp-reddit",type:"mcp",desc:"Reddit",category:"social",free:true,needs:"REDDIT_TOKEN"},
{id:"mcp-shopify",name:"Shopify MCP",url:"npx -y @shopify/mcp-server",type:"mcp",desc:"Shopify",category:"commerce",free:true,needs:"SHOPIFY_TOKEN"},
{id:"mcp-airtable",name:"Airtable MCP",url:"npx -y airtable-mcp-server",type:"mcp",desc:"Airtable",category:"data",free:true,needs:"AIRTABLE_KEY"},
{id:"mcp-zapier",name:"Zapier MCP",url:"npx -y @zapier/mcp",type:"mcp",desc:"7000+ apps",category:"automation",free:true,needs:"ZAPIER_KEY"},
{id:"mcp-obsidian",name:"Obsidian MCP",url:"npx -y mcp-obsidian",type:"mcp",desc:"Obsidian",category:"productivity",free:true},
{id:"mcp-pinecone",name:"Pinecone MCP",url:"npx -y mcp-pinecone",type:"mcp",desc:"Vector DB",category:"ai",free:true,needs:"PINECONE_KEY"},
{id:"mcp-arxiv",name:"arXiv MCP",url:"npx -y arxiv-mcp-server",type:"mcp",desc:"Papers",category:"research",free:true},
{id:"mcp-git",name:"Git MCP",url:"npx -y @cyanheads/git-mcp-server",type:"mcp",desc:"Git",category:"devops",free:true},
{id:"mcp-1password",name:"1Password MCP",url:"npx -y @1password/mcp-server",type:"mcp",desc:"Secrets",category:"security",free:true,needs:"OP_TOKEN"},
{id:"mcp-resend",name:"Resend MCP",url:"npx -y resend-mcp",type:"mcp",desc:"Email",category:"comms",free:true,needs:"RESEND_KEY"},
{id:"mcp-twilio",name:"Twilio MCP",url:"npx -y twilio-mcp",type:"mcp",desc:"SMS",category:"comms",free:true,needs:"TWILIO_TOKEN"},
{id:"mcp-posthog",name:"PostHog MCP",url:"npx -y @posthog/mcp",type:"mcp",desc:"Analytics",category:"analytics",free:true,needs:"POSTHOG_KEY"},
{id:"mcp-perplexity",name:"Perplexity MCP",url:"npx -y server-perplexity-ask",type:"mcp",desc:"Perplexity",category:"search",free:true,needs:"PERPLEXITY_KEY"}
],
llm:[
{id:"groq",name:"Groq",url:"https://api.groq.com/openai/v1",type:"llm",desc:"Fastest inference",category:"ai",free:true},
{id:"cerebras",name:"Cerebras",url:"https://api.cerebras.ai/v1",type:"llm",desc:"Fastest 70b",category:"ai",free:true},
{id:"sambanova",name:"SambaNova",url:"https://api.sambanova.ai/v1",type:"llm",desc:"Llama 405b",category:"ai",free:true},
{id:"openrouter",name:"OpenRouter",url:"https://openrouter.ai/api/v1",type:"llm",desc:"100+ models",category:"ai",free:true},
{id:"google_ai",name:"Google AI Studio",url:"https://generativelanguage.googleapis.com/v1beta",type:"llm",desc:"Gemini",category:"ai",free:true},
{id:"github_models",name:"GitHub Models",url:"https://models.inference.ai.azure.com",type:"llm",desc:"GPT-4o, Llama",category:"ai",free:true},
{id:"mistral",name:"Mistral",url:"https://api.mistral.ai/v1",type:"llm",desc:"Free tier",category:"ai",free:true},
{id:"cohere",name:"Cohere",url:"https://api.cohere.com/v2",type:"llm",desc:"Command R+",category:"ai",free:true},
{id:"together",name:"Together.ai",url:"https://api.together.xyz/v1",type:"llm",desc:"Open + images",category:"ai",free:true},
{id:"hf",name:"HuggingFace",url:"https://api-inference.huggingface.co/models",type:"llm",desc:"Thousands",category:"ai",free:true},
{id:"cloudflare_ai",name:"CF Workers AI",url:"https://api.cloudflare.com/client/v4/ai",type:"llm",desc:"Edge",category:"ai",free:true},
{id:"nvidia_nim",name:"NVIDIA NIM",url:"https://integrate.api.nvidia.com/v1",type:"llm",desc:"Free creds",category:"ai",free:true},
{id:"deepinfra",name:"DeepInfra",url:"https://api.deepinfra.com/v1/openai",type:"llm",desc:"Open models",category:"ai",free:true},
{id:"fireworks",name:"Fireworks",url:"https://api.fireworks.ai/inference/v1",type:"llm",desc:"Fast",category:"ai",free:true},
{id:"deepseek",name:"DeepSeek",url:"https://api.deepseek.com/v1",type:"llm",desc:"V3, R1",category:"ai",free:true},
{id:"xai",name:"xAI Grok",url:"https://api.x.ai/v1",type:"llm",desc:"Grok 2",category:"ai",free:true},
{id:"ollama",name:"Ollama",url:"http://localhost:11434/v1",type:"llm",desc:"Local",category:"ai",free:true},
{id:"lmstudio",name:"LM Studio",url:"http://localhost:1234/v1",type:"llm",desc:"Local",category:"ai",free:true},
{id:"pollinations",name:"Pollinations",url:"https://text.pollinations.ai/openai",type:"llm",desc:"No key",category:"ai",free:true},
{id:"llm7",name:"LLM7.io",url:"https://api.llm7.io/v1",type:"llm",desc:"Free proxy",category:"ai",free:true}
],
api:[
{id:"tavily",name:"Tavily Search",url:"https://api.tavily.com/search",type:"api",desc:"AI search",category:"search",free:true,needs:"TAVILY_KEY"},
{id:"serper",name:"Serper",url:"https://google.serper.dev/search",type:"api",desc:"Google SERP",category:"search",free:true,needs:"SERPER_KEY"},
{id:"jina",name:"Jina Reader",url:"https://r.jina.ai/",type:"api",desc:"URL to MD",category:"web",free:true},
{id:"firecrawl",name:"Firecrawl",url:"https://api.firecrawl.dev/v1",type:"api",desc:"Scraping",category:"web",free:true,needs:"FIRECRAWL_KEY"},
{id:"github_api",name:"GitHub API",url:"https://api.github.com",type:"api",desc:"Repos",category:"devops",free:true,needs:"GH_TOKEN"},
{id:"vercel_api",name:"Vercel API",url:"https://api.vercel.com",type:"api",desc:"Deploys",category:"devops",free:true,needs:"VERCEL_TOKEN"},
{id:"netlify_api",name:"Netlify API",url:"https://api.netlify.com/api/v1",type:"api",desc:"Sites",category:"devops",free:true,needs:"NETLIFY_TOKEN"},
{id:"resend",name:"Resend",url:"https://api.resend.com",type:"api",desc:"Email",category:"comms",free:true,needs:"RESEND_KEY"},
{id:"sendgrid",name:"SendGrid",url:"https://api.sendgrid.com/v3",type:"api",desc:"Email",category:"comms",free:true,needs:"SG_KEY"},
{id:"twilio",name:"Twilio",url:"https://api.twilio.com/2010-04-01",type:"api",desc:"SMS",category:"comms",free:true,needs:"TWILIO_TOKEN"},
{id:"upstash",name:"Upstash Redis",url:"https://api.upstash.com",type:"api",desc:"Serverless Redis",category:"data",free:true,needs:"UPSTASH_TOKEN"},
{id:"neon",name:"Neon",url:"https://console.neon.tech/api/v2",type:"api",desc:"Serverless Postgres",category:"data",free:true,needs:"NEON_KEY"},
{id:"turso",name:"Turso",url:"https://api.turso.tech/v1",type:"api",desc:"Edge SQLite",category:"data",free:true,needs:"TURSO_TOKEN"},
{id:"convex",name:"Convex",url:"https://api.convex.dev",type:"api",desc:"Realtime DB",category:"data",free:true,needs:"CONVEX_TOKEN"},
{id:"discord",name:"Discord Bot",url:"https://discord.com/api/v10",type:"api",desc:"Bots",category:"social",free:true,needs:"DISCORD_TOKEN"},
{id:"telegram",name:"Telegram Bot",url:"https://api.telegram.org/bot",type:"api",desc:"Bots",category:"social",free:true,needs:"TELEGRAM_TOKEN"},
{id:"twitter",name:"Twitter API",url:"https://api.twitter.com/2",type:"api",desc:"Posts",category:"social",free:true,needs:"TWITTER_BEARER"},
{id:"reddit",name:"Reddit API",url:"https://oauth.reddit.com",type:"api",desc:"Posts",category:"social",free:true,needs:"REDDIT_TOKEN"},
{id:"bluesky",name:"Bluesky",url:"https://bsky.social/xrpc",type:"api",desc:"AT Proto",category:"social",free:true,needs:"BSKY_PASS"},
{id:"linkedin",name:"LinkedIn",url:"https://api.linkedin.com/v2",type:"api",desc:"Posts",category:"social",free:true,needs:"LI_TOKEN"},
{id:"youtube",name:"YouTube Data",url:"https://www.googleapis.com/youtube/v3",type:"api",desc:"Videos",category:"social",free:true,needs:"YT_KEY"},
{id:"replicate",name:"Replicate",url:"https://api.replicate.com/v1",type:"api",desc:"AI models",category:"ai",free:true,needs:"REPLICATE_TOKEN"},
{id:"elevenlabs",name:"ElevenLabs",url:"https://api.elevenlabs.io/v1",type:"api",desc:"TTS",category:"ai",free:true,needs:"ELEVEN_KEY"},
{id:"deepgram",name:"Deepgram",url:"https://api.deepgram.com/v1",type:"api",desc:"STT",category:"ai",free:true,needs:"DG_KEY"},
{id:"openweather",name:"OpenWeather",url:"https://api.openweathermap.org/data/2.5",type:"api",desc:"Weather",category:"data",free:true,needs:"OWM_KEY"},
{id:"newsapi",name:"NewsAPI",url:"https://newsapi.org/v2",type:"api",desc:"News",category:"data",free:true,needs:"NEWS_KEY"},
{id:"coingecko",name:"CoinGecko",url:"https://api.coingecko.com/api/v3",type:"api",desc:"Crypto",category:"data",free:true},
{id:"stripe_api",name:"Stripe",url:"https://api.stripe.com/v1",type:"api",desc:"Payments",category:"commerce",free:true,needs:"STRIPE_KEY"},
{id:"polar",name:"Polar.sh",url:"https://api.polar.sh/v1",type:"api",desc:"Subs",category:"commerce",free:true,needs:"POLAR_TOKEN"},
{id:"lemonsqueezy",name:"LemonSqueezy",url:"https://api.lemonsqueezy.com/v1",type:"api",desc:"MoR",category:"commerce",free:true,needs:"LS_TOKEN"},
{id:"unsplash",name:"Unsplash",url:"https://api.unsplash.com",type:"api",desc:"Photos",category:"creative",free:true,needs:"UNSPLASH_KEY"},
{id:"crossref",name:"CrossRef",url:"https://api.crossref.org",type:"api",desc:"DOIs",category:"research",free:true},
{id:"openalex",name:"OpenAlex",url:"https://api.openalex.org",type:"api",desc:"200M papers",category:"research",free:true},
{id:"semantic_scholar",name:"Semantic Scholar",url:"https://api.semanticscholar.org/graph/v1",type:"api",desc:"Citations",category:"research",free:true}
],
tunnel:[
{id:"serveo",name:"Serveo",url:"ssh -R 80:localhost:PORT serveo.net",type:"tunnel",desc:"SSH tunnel",category:"network",free:true},
{id:"localhost_run",name:"localhost.run",url:"ssh -R 80:localhost:PORT localhost.run",type:"tunnel",desc:"SSH tunnel",category:"network",free:true}
]};

// Exports for cli.mjs, etc.
export var toggleK = function(){
  // K-factor toggling
};
export var toggleTier = function(){
  // Tier toggling
};
export var dumpChain = function(){
  // Dump chain
};
export var dumpBrofile = function(){
  // Dump brofile
};
export var handleTgCommand = function(input){
  // Telegram commands
};
export var ghostObserve = function(input){
  // Ghost observe
};