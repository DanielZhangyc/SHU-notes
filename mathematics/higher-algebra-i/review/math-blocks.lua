function CodeBlock(element)
  if element.classes:includes('math') then
    return pandoc.Para({pandoc.Math('DisplayMath', element.text)})
  end
end

function Header(element)
  if element.level <= 2 then
    return {pandoc.RawBlock('latex', '\\Needspace{7\\baselineskip}'), element}
  end
end
