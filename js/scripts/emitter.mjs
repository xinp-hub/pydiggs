// JSON Schema -> TypeScript, for scripts/generate.mjs: the type expressions and named
// declarations of the contracts' schemas. It handles the JSON Schema keywords these
// contracts use and stops on any other, so a new keyword is a decision here and never a
// silently wrong type. Separate from generate.mjs, which reads and writes files as soon
// as it is loaded, so that test/generate.test.ts can hold the refusals to their word.

// Keywords that only constrain values a TypeScript type cannot express.
const VALIDATION_ONLY = new Set([
  "pattern",
  "minLength",
  "maxLength",
  "minimum",
  "maximum",
  "minItems",
  "maxItems",
  "format",
  "contentMediaType",
  "contentEncoding",
  "exclusiveMinimum",
  "exclusiveMaximum",
]);
const ANNOTATIONS = new Set(["title", "description", "examples", "default", "$schema", "$id", "$defs"]);
const STRUCTURAL = new Set([
  "type",
  "properties",
  "required",
  "additionalProperties",
  "items",
  "enum",
  "const",
  "$ref",
  "anyOf",
  "allOf",
]);

function checkKeywords(schema, where) {
  for (const key of Object.keys(schema)) {
    if (!STRUCTURAL.has(key) && !VALIDATION_ONLY.has(key) && !ANNOTATIONS.has(key)) {
      throw new Error(`${where}: unsupported JSON Schema keyword "${key}"`);
    }
  }
}

const propertyKey = (name) => (/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? name : JSON.stringify(name));

function jsdoc(schema, indent) {
  const lines = [];
  if (typeof schema.description === "string") lines.push(schema.description);
  if (Array.isArray(schema.examples) && schema.examples.length > 0) {
    lines.push(`Examples: ${schema.examples.map((example) => JSON.stringify(example)).join(", ")}.`);
  }
  if (lines.length === 0) return "";
  const text = lines.join(" ").replaceAll("*/", "*\\/");
  return `${indent}/** ${text} */\n`;
}

// An `allOf` entry that is an if/then/else refinement narrows values the base type
// already allows, so it adds nothing to the TypeScript type.
const isRefinement = (entry) => "if" in entry;

export class Emitter {
  /** @param {(ref: string) => string} resolveRef */
  constructor(resolveRef) {
    this.resolveRef = resolveRef;
  }

  /** The type expression for `schema`, written at `indent`. */
  type(schema, where, indent = "") {
    if (schema === true) return "unknown";
    checkKeywords(schema, where);
    if ("$ref" in schema) return this.resolveRef(schema.$ref);
    if ("const" in schema) return JSON.stringify(schema.const);
    if ("enum" in schema) return schema.enum.map((value) => JSON.stringify(value)).join(" | ");
    if ("anyOf" in schema) {
      return schema.anyOf.map((part, i) => this.type(part, `${where}/anyOf/${i}`, indent)).join(" | ");
    }
    if ("allOf" in schema && !schema.allOf.every(isRefinement)) {
      throw new Error(`${where}: allOf is supported only as the base of a named definition`);
    }
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (types[0] === undefined) {
      return "properties" in schema ? this.object(schema, where, indent) : "unknown";
    }
    return types.map((type) => this.single(type, schema, where, indent)).join(" | ");
  }

  single(type, schema, where, indent) {
    switch (type) {
      case "null":
        return "null";
      case "string":
        return "string";
      case "boolean":
        return "boolean";
      case "integer":
      case "number":
        return "number";
      case "array": {
        if (!("items" in schema)) return "unknown[]";
        const item = this.type(schema.items, `${where}/items`, indent);
        return /[ |]/.test(item) && !item.startsWith("{") ? `(${item})[]` : `${item}[]`;
      }
      case "object":
        return this.object(schema, where, indent);
      default:
        throw new Error(`${where}: unsupported type "${type}"`);
    }
  }

  object(schema, where, indent) {
    const body = this.members(schema, where, `${indent}  `);
    return body === "" ? "{ [key: string]: unknown }" : `{\n${body}${indent}}`;
  }

  /** The members of an object type, one per line, each at `indent`. */
  members(schema, where, indent) {
    const required = new Set(schema.required ?? []);
    const properties = Object.entries(schema.properties ?? {});
    const extra = schema.additionalProperties;
    // A required name with no schema of its own takes the additionalProperties schema:
    // an open map that always holds these keys, as TablesToDiggsResult's `counts`.
    // Without such a schema, the name is a mistake in the contract.
    const open = extra !== undefined && extra !== false && extra !== true;
    for (const name of required) {
      if (!(name in (schema.properties ?? {}))) {
        if (!open) throw new Error(`${where}: required property "${name}" is not declared`);
        properties.push([name, extra]);
      }
    }
    let out = "";
    let anyOptional = false;
    for (const [name, property] of properties) {
      const optional = !required.has(name);
      anyOptional ||= optional;
      out += jsdoc(property, indent);
      out += `${indent}${propertyKey(name)}${optional ? "?" : ""}: ${this.type(property, `${where}/${name}`, indent)};\n`;
    }
    if (extra !== undefined && extra !== false) {
      const value = this.type(extra, `${where}/additionalProperties`, indent);
      out += `${indent}[key: string]: ${value}${anyOptional ? " | undefined" : ""};\n`;
    }
    return out;
  }

  /** A named, exported declaration for `schema`. */
  declaration(name, schema, where) {
    checkKeywords(schema, where);
    const bases = (schema.allOf ?? []).filter((entry) => !isRefinement(entry));
    const isObject = schema.type === "object" && ("properties" in schema || bases.length > 0);
    if (!isObject) {
      if (bases.length > 0) throw new Error(`${where}: allOf on a non-object definition`);
      return `${jsdoc(schema, "")}export type ${name} = ${this.type({ ...schema, description: undefined, examples: undefined }, where)};\n`;
    }
    const extended = bases.map((base, i) => {
      if (Object.keys(base).length !== 1 || !("$ref" in base)) {
        throw new Error(`${where}/allOf/${i}: only a bare $ref can be a base`);
      }
      return this.resolveRef(base.$ref);
    });
    const heritage = extended.length > 0 ? ` extends ${extended.join(", ")}` : "";
    return `${jsdoc(schema, "")}export interface ${name}${heritage} {\n${this.members(schema, where, "  ")}}\n`;
  }
}
